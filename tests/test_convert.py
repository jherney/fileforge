"""Conversion tests: every pair registered in converters.REGISTRY is run once and the
output checked, plus content checks for the riskiest paths and the HTTP API.

Needs LibreOffice (soffice) on PATH and httpx for the API tests:
    python3 -m unittest tests.test_convert        # ~1 minute, most of it LibreOffice
"""
import io
import sys
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pillow_heif  # noqa: E402
import pymupdf  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

import app as A  # noqa: E402
import converters as C  # noqa: E402

FODP = b"""<?xml version="1.0" encoding="UTF-8"?>
<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
 office:version="1.2" office:mimetype="application/vnd.oasis.opendocument.presentation">
<office:body><office:presentation><draw:page draw:name="p1">
<draw:frame svg:width="20cm" svg:height="3cm" svg:x="2cm" svg:y="2cm">
<draw:text-box><text:p>Hello FileForge</text:p></draw:text-box></draw:frame>
</draw:page></office:presentation></office:body></office:document>"""

SAMPLES: dict[str, bytes] = {}


def setUpModule():
    txt = b"Hello FileForge\nsecond line\n"
    SAMPLES["txt"] = txt
    SAMPLES["html"] = (b"<html><body><h1>Hello FileForge</h1>"
                       b"<table><tr><td>1</td><td>2</td></tr></table></body></html>")
    SAMPLES["csv"] = b"a,b\n1,2\n3,4\n"
    jobs = {
        "doc": (txt, "txt", "doc"), "docx": (txt, "txt", "docx"),
        "odt": (txt, "txt", "odt"), "rtf": (txt, "txt", "rtf"),
        "pdf": (txt, "txt", "pdf"),
        "xls": (SAMPLES["csv"], "csv", "xls"), "xlsx": (SAMPLES["csv"], "csv", "xlsx"),
        "ods": (SAMPLES["csv"], "csv", "ods"),
        "odp": (FODP, "fodp", "odp"), "pptx": (FODP, "fodp", "pptx"),
        "ppt": (FODP, "fodp", "ppt"),
    }
    with ThreadPoolExecutor(4) as ex:
        futs = {k: ex.submit(C.soffice_convert, *args) for k, args in jobs.items()}
        for k, f in futs.items():
            SAMPLES[k] = f.result()
    pillow_heif.register_heif_opener()
    im = Image.new("RGBA", (40, 30), (255, 0, 0, 128))
    for ext, fmt in [("png", "PNG"), ("webp", "WEBP"), ("gif", "GIF"), ("bmp", "BMP"),
                     ("tiff", "TIFF"), ("heic", "HEIF"), ("heif", "HEIF")]:
        b = io.BytesIO()
        im.save(b, fmt)
        SAMPLES[ext] = b.getvalue()
    b = io.BytesIO()
    im.convert("RGB").save(b, "JPEG")
    SAMPLES["jpg"] = SAMPLES["jpeg"] = b.getvalue()


def looks_valid(ext: str, d: bytes) -> bool:
    if len(d) == 0:
        return False
    zips = {"docx", "xlsx", "pptx", "odt", "ods", "odp", "zip"}
    if ext in zips:
        return d[:2] == b"PK" and zipfile.is_zipfile(io.BytesIO(d))
    return {
        "pdf": d.startswith(b"%PDF"),
        "png": d.startswith(b"\x89PNG"),
        "jpg": d.startswith(b"\xff\xd8"),
        "gif": d.startswith(b"GIF8"),
        "bmp": d.startswith(b"BM"),
        "webp": d[:4] == b"RIFF" and d[8:12] == b"WEBP",
        "tiff": d[:4] in (b"II*\x00", b"MM\x00*"),
        "rtf": d.startswith(b"{\\rtf"),
        "doc": d.startswith(b"\xd0\xcf\x11\xe0"),
        "xls": d.startswith(b"\xd0\xcf\x11\xe0"),
        "ppt": d.startswith(b"\xd0\xcf\x11\xe0"),
        "txt": bool(d.strip()),
        "csv": bool(d.strip()),
        "html": b"<" in d,
    }.get(ext, True)


class RegistryMatrix(unittest.TestCase):
    def test_every_source_has_a_sample(self):
        missing = {s for (s, _) in C.REGISTRY} - set(SAMPLES)
        self.assertFalse(missing, f"add a sample for: {sorted(missing)}")

    def test_every_registered_pair_produces_valid_output(self):
        def run(pair):
            src, dst = pair
            try:
                out = C.REGISTRY[pair].run(SAMPLES[src])
            except Exception as exc:
                return f"{src}->{dst}: raised {exc}"
            return None if looks_valid(dst, out) else f"{src}->{dst}: output is not a valid {dst}"
        with ThreadPoolExecutor(4) as ex:
            bad = [r for r in ex.map(run, sorted(C.REGISTRY)) if r]
        self.assertEqual(bad, [])


class Content(unittest.TestCase):
    def test_html_to_docx_keeps_text_and_table(self):  # was broken: no export filter
        d = C.REGISTRY[("html", "docx")].run(SAMPLES["html"])
        with zipfile.ZipFile(io.BytesIO(d)) as z:
            xml = z.read("word/document.xml").decode()
        self.assertIn("Hello FileForge", xml)
        self.assertIn("<w:tbl>", xml)

    def test_pdf_to_txt_keeps_text(self):
        out = C.REGISTRY[("pdf", "txt")].run(SAMPLES["pdf"]).decode()
        self.assertIn("Hello FileForge", out)

    def test_pdf_to_docx_keeps_text(self):
        d = C.REGISTRY[("pdf", "docx")].run(SAMPLES["pdf"])
        with zipfile.ZipFile(io.BytesIO(d)) as z:
            self.assertIn("Hello", z.read("word/document.xml").decode())

    def test_pdf_to_png_is_first_page_image(self):
        out = C.REGISTRY[("pdf", "png")].run(SAMPLES["pdf"])
        w, h = Image.open(io.BytesIO(out)).size
        self.assertGreater(w, 500)
        self.assertGreater(h, 500)

    def test_pdf_to_zip_has_every_page(self):
        doc = pymupdf.open()
        for n in range(3):
            doc.new_page().insert_text((72, 72), f"page {n + 1}")
        three_pages = doc.tobytes()
        out = C.REGISTRY[("pdf", "zip")].run(three_pages)
        with zipfile.ZipFile(io.BytesIO(out)) as z:
            self.assertEqual(z.namelist(), ["page-001.png", "page-002.png", "page-003.png"])
            for name in z.namelist():
                self.assertTrue(z.read(name).startswith(b"\x89PNG"))

    def test_docx_to_txt_keeps_text(self):
        self.assertIn("Hello FileForge", C.REGISTRY[("docx", "txt")].run(SAMPLES["docx"]).decode())

    def test_csv_to_xlsx_to_csv_roundtrip(self):
        x = C.REGISTRY[("csv", "xlsx")].run(SAMPLES["csv"])
        back = C.REGISTRY[("xlsx", "csv")].run(x).decode()
        self.assertIn("3,4", back)

    def test_pptx_to_pdf_has_slide_text(self):
        pdf = C.REGISTRY[("pptx", "pdf")].run(SAMPLES["pptx"])
        doc = pymupdf.open(stream=pdf, filetype="pdf")
        self.assertIn("Hello FileForge", doc[0].get_text())

    def test_heic_decodes_to_same_size(self):
        for dst in ("jpg", "png"):
            out = C.REGISTRY[("heic", dst)].run(SAMPLES["heic"])
            self.assertEqual(Image.open(io.BytesIO(out)).size, (40, 30))

    def test_transparent_png_to_jpg_flattens(self):
        out = C.REGISTRY[("png", "jpg")].run(SAMPLES["png"])
        self.assertEqual(Image.open(io.BytesIO(out)).mode, "RGB")

    def test_corrupt_image_raises(self):
        with self.assertRaisesRegex(ValueError, "cannot read this image"):
            C.REGISTRY[("png", "jpg")].run(b"not an image")


class Chains(unittest.TestCase):
    def test_direct_pair_is_one_hop(self):
        for pair in C.REGISTRY:
            self.assertEqual(C.find_route(*pair), [pair])

    def test_known_routes(self):
        self.assertEqual(C.find_route("docx", "png"), [("docx", "pdf"), ("pdf", "png")])
        self.assertEqual(C.find_route("pdf", "jpg"), [("pdf", "png"), ("png", "jpg")])
        self.assertEqual(C.find_route("heic", "pdf"), [("heic", "pdf")])

    def test_unreachable_is_none(self):
        self.assertIsNone(C.find_route("png", "xyz"))
        self.assertIsNone(C.find_route("xyz", "png"))

    def test_every_route_is_valid(self):
        sources = {a for (a, _) in C.REGISTRY}
        targets = {b for (_, b) in C.REGISTRY}
        for src in sources:
            for dst in targets - {src}:
                route = C.find_route(src, dst)
                if route is None:
                    continue
                path = [route[0][0]] + [b for _, b in route]
                self.assertEqual((path[0], path[-1]), (src, dst))
                self.assertLessEqual(len(route), C.MAX_HOPS)
                self.assertEqual(len(set(path)), len(path), f"loop in {path}")
                self.assertTrue(all(hop in C.REGISTRY for hop in route), path)
                self.assertTrue(all(a == b0 for (_, a), (b0, _) in zip(route, route[1:])), path)
                self.assertFalse(set(path[1:-1]) & C.LOSSY_INTERMEDIATES, f"lossy stop in {path}")
                if (src, dst) in C.REGISTRY:
                    self.assertEqual(len(route), 1)

    def test_chained_pairs_exclude_direct_ones(self):
        chained = C.chained_pairs()
        self.assertTrue(chained)
        self.assertFalse(set(chained) & set(C.REGISTRY))
        self.assertEqual(chained[("docx", "png")], [("docx", "pdf"), ("pdf", "png")])

    def test_images_only_chain_to_images(self):
        for (src, dst) in C.chained_pairs():
            if src in C.IMAGE_SOURCES:
                self.assertIn(dst, C.IMAGE_SOURCES, f"{src}->{dst}")
        for dst in ("txt", "zip", "docx", "html"):
            self.assertIsNone(C.find_route("png", dst))
        self.assertEqual(C.find_route("png", "pdf"), [("png", "pdf")])  # direct stays

    def test_empty_result_is_an_error_not_an_empty_file(self):
        route = [("pdf", "txt")]
        for empty in (b"", b" \n\n"):
            with mock.patch.dict(C.REGISTRY, {("pdf", "txt"): C.Conversion("pdf", "txt", "text/plain", lambda d, e=empty: e)}):
                with self.assertRaisesRegex(ValueError, "empty"):
                    C.run_chain(b"x", route)

    def test_pdf_to_jpg_runs_through_png(self):
        out = C.run_chain(SAMPLES["pdf"], C.find_route("pdf", "jpg"))
        self.assertTrue(out.startswith(b"\xff\xd8"))

    def test_docx_to_png_runs_through_pdf(self):
        out = C.run_chain(SAMPLES["docx"], C.find_route("docx", "png"))
        self.assertTrue(out.startswith(b"\x89PNG"))

    def test_failure_names_the_hop(self):
        real = C.REGISTRY[("png", "jpg")]
        broken = type(real)("png", "jpg", real.content_type, lambda data: (_ for _ in ()).throw(RuntimeError("boom")))
        with mock.patch.dict(C.REGISTRY, {("png", "jpg"): broken}):
            with self.assertRaisesRegex(RuntimeError, r"png->jpg failed \(step 2 of 2\): boom"):
                C.run_chain(SAMPLES["pdf"], C.find_route("pdf", "jpg"))

    def test_oversize_intermediate_is_refused(self):
        with mock.patch.object(C, "MAX_BYTES", 10):
            with self.assertRaisesRegex(RuntimeError, "intermediate result is over"):
                C.run_chain(SAMPLES["pdf"], C.find_route("pdf", "jpg"))

    def test_image_to_pdf_page_follows_image_shape_and_flattens_transparency(self):
        buf = io.BytesIO()
        Image.new("RGBA", (200, 100), (0, 0, 0, 0)).save(buf, "PNG")   # fully transparent
        pdf = C.REGISTRY[("png", "pdf")].run(buf.getvalue())
        doc = pymupdf.open(stream=pdf, filetype="pdf")
        self.assertAlmostEqual(doc[0].rect.width / doc[0].rect.height, 2.0, places=2)
        px = doc[0].get_pixmap(dpi=36)
        self.assertEqual(tuple(px.pixel(1, 1))[:3], (255, 255, 255))  # white, not black


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(A.app, base_url="http://127.0.0.1:8420")

    def post(self, name, data, src, dst, headers=None):
        return self.c.post(f"/api/convert?fmt_from={src}&fmt_to={dst}",
                           files={"file": (name, data)}, headers=headers)

    def test_formats_lists_heic_and_pdf_pairs(self):
        pairs = {(p["from"], p["to"]) for p in self.c.get("/api/formats").json()["pairs"]}
        self.assertIn(("heic", "jpg"), pairs)
        self.assertIn(("pdf", "docx"), pairs)

    def test_formats_lists_chained_pairs_with_via(self):
        pairs = self.c.get("/api/formats").json()["pairs"]
        chained = [p for p in pairs if (p["from"], p["to"]) == ("docx", "png")]
        self.assertEqual(chained, [{"from": "docx", "to": "png", "via": ["pdf"]}])
        direct = [p for p in pairs if (p["from"], p["to"]) == ("png", "jpg")]
        self.assertEqual(direct, [{"from": "png", "to": "jpg"}])

    def test_chained_convert_works(self):
        r = self.post("scan.pdf", SAMPLES["pdf"], "pdf", "jpg")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/jpeg")
        self.assertEqual(r.headers["x-download-filename"], "scan.jpg")
        self.assertTrue(r.content.startswith(b"\xff\xd8"))

    def test_convert_ok_with_download_headers(self):
        r = self.post("photo.png", SAMPLES["png"], "png", "jpg")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/jpeg")
        self.assertEqual(r.headers["x-download-filename"], "photo.jpg")

    def test_filename_is_sanitised(self):
        r = self.post('../../evil"name.png', SAMPLES["png"], "png", "jpg")
        name = r.headers["x-download-filename"]
        self.assertTrue(name.endswith(".jpg"))
        self.assertNotIn("/", name)
        self.assertNotIn('"', name)

    def test_unsupported_pair_is_400(self):
        self.assertEqual(self.post("a.png", SAMPLES["png"], "png", "xyz").status_code, 400)

    def test_empty_file_is_400(self):
        self.assertEqual(self.post("a.png", b"", "png", "jpg").status_code, 400)

    def test_oversize_is_413(self):
        with mock.patch.object(A, "MAX_BYTES", 10):
            self.assertEqual(self.post("a.png", SAMPLES["png"], "png", "jpg").status_code, 413)

    def test_cross_site_origin_is_refused(self):
        r = self.post("a.png", SAMPLES["png"], "png", "jpg", headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_local_origin_and_no_origin_are_accepted(self):
        for hdr in ({"Origin": "http://localhost:8420"}, {}):
            r = self.post("a.png", SAMPLES["png"], "png", "jpg", headers=hdr)
            self.assertEqual(r.status_code, 200, hdr)

    def test_foreign_host_header_is_refused(self):  # DNS-rebinding case
        r = self.c.get("/api/formats", headers={"Host": "evil.example"})
        self.assertEqual(r.status_code, 400)

    def test_declared_huge_upload_is_refused_before_parsing(self):
        r = self.post("a.png", SAMPLES["png"], "png", "jpg",
                      headers={"Content-Length": str(A.MAX_BYTES + 5 * 1024 * 1024)})
        self.assertEqual(r.status_code, 413)

    def test_body_over_limit_is_refused_even_if_header_lies(self):
        with mock.patch.object(A, "MAX_BYTES", 10):
            r = self.post("a.png", SAMPLES["png"], "png", "jpg")
        self.assertEqual(r.status_code, 413)

    def test_corrupt_file_is_422(self):
        self.assertEqual(self.post("a.png", b"not an image", "png", "jpg").status_code, 422)


if __name__ == "__main__":
    unittest.main()
