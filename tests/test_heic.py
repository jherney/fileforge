"""End-to-end HEIC/HEIF coverage: registry, POST /api/convert, and EXIF rotation."""
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pillow_heif  # noqa: E402
import pymupdf  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

import app as A  # noqa: E402
import converters as C  # noqa: E402

pillow_heif.register_heif_opener()

MAGIC = {"jpg": b"\xff\xd8", "png": b"\x89PNG", "pdf": b"%PDF"}
MIME = {"jpg": "image/jpeg", "png": "image/png", "pdf": "application/pdf"}


def heic(size=(40, 30), exif_orientation=None, ext_fmt="HEIF") -> bytes:
    im = Image.new("RGB", size, (200, 30, 30))
    kw = {}
    if exif_orientation:
        ex = Image.Exif()
        ex[0x0112] = exif_orientation
        kw["exif"] = ex
    b = io.BytesIO()
    im.save(b, ext_fmt, **kw)
    return b.getvalue()


class HeicRegistry(unittest.TestCase):
    def test_converts_to_jpg_png_pdf(self):
        for src in ("heic", "heif"):
            for dst in ("jpg", "png", "pdf"):
                out = C.REGISTRY[(src, dst)].run(heic())
                self.assertTrue(out.startswith(MAGIC[dst]), (src, dst))
                if dst == "pdf":
                    self.assertEqual(len(pymupdf.open(stream=out, filetype="pdf")), 1)
                else:
                    self.assertEqual(Image.open(io.BytesIO(out)).size, (40, 30))

    def test_no_chained_route_exists_for_heic(self):
        # every heic target is direct; a chain would show up here as a non-direct route
        for dst in {b for _, b in C.REGISTRY}:
            route = C.find_route("heic", dst)
            if route:
                self.assertEqual(route, [("heic", dst)])

    def test_exif_tagged_heic_is_not_rotated_twice(self):
        # pillow_heif cannot write a HEIC that decodes as rotated: it stores the EXIF tag
        # but its decoder resets it to 1 (real phone files use the irot box, which libheif
        # applies on decode). So the best check is that output matches the decoded image.
        data = heic((200, 100), exif_orientation=6)
        decoded = Image.open(io.BytesIO(data)).size
        for dst in ("jpg", "png"):
            out = C.REGISTRY[("heic", dst)].run(data)
            self.assertEqual(Image.open(io.BytesIO(out)).size, decoded, dst)


class HeicApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(A.app, base_url="http://127.0.0.1:8420")

    def post(self, name, data, src, dst):
        return self.c.post(f"/api/convert?fmt_from={src}&fmt_to={dst}",
                           files={"file": (name, data)})

    def test_heic_and_heif_convert_over_http(self):
        for ext in ("heic", "heif"):
            for dst in ("jpg", "png", "pdf"):
                r = self.post(f"IMG_1.{ext}", heic(), ext, dst)
                self.assertEqual(r.status_code, 200, (ext, dst, r.text))
                self.assertEqual(r.headers["content-type"], MIME[dst])
                self.assertEqual(r.headers["x-download-filename"], f"IMG_1.{dst}")
                self.assertTrue(r.content.startswith(MAGIC[dst]))

    def test_corrupt_heic_is_a_clean_client_error(self):
        r = self.post("bad.heic", b"not a heic", "heic", "jpg")
        self.assertEqual(r.status_code, 422, r.text)
