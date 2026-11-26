"""Regression tests for the review findings: orientation, colour modes, size caps,
LibreOffice kill, symlinks, atomic output names, Origin, watcher resilience."""
import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

import converters as C  # noqa: E402
import pymupdf  # noqa: E402
import watcher  # noqa: E402

watcher.notify = lambda *a, **k: None


def enc(im, fmt, **kw) -> bytes:
    b = io.BytesIO()
    im.save(b, fmt, **kw)
    return b.getvalue()


def size_of(data: bytes):
    return Image.open(io.BytesIO(data)).size


class Images(unittest.TestCase):
    def test_exif_orientation_is_applied(self):
        im = Image.new("RGB", (200, 100), "red")
        exif = Image.Exif()
        exif[0x0112] = 6  # stored sideways, display rotated 90 degrees
        jpg = enc(im, "JPEG", exif=exif)
        for dst in ("png", "webp"):
            self.assertEqual(size_of(C.image_convert(jpg, "jpg", dst)), (100, 200), dst)
        pdf = pymupdf.open(stream=C.image_to_pdf(jpg, "jpg"), filetype="pdf")
        self.assertLess(pdf[0].rect.width, pdf[0].rect.height)

    def test_modes_the_target_cannot_hold_are_converted(self):
        cases = [("tiff", "TIFF", Image.new("CMYK", (8, 8))),
                 ("tiff", "TIFF", Image.new("LA", (8, 8))),
                 ("png", "PNG", Image.new("I;16", (8, 8), 60000))]
        for src, fmt, im in cases:
            data = enc(im, fmt)
            for dst in C.IMAGE_SOURCES[src]:
                with self.subTest(mode=im.mode, dst=dst):
                    self.assertEqual(size_of(C.image_convert(data, src, dst)), (8, 8))

    def test_16_bit_is_scaled_not_clipped(self):
        data = enc(Image.new("I;16", (4, 4), 32768), "PNG")
        out = Image.open(io.BytesIO(C.image_convert(data, "png", "bmp"))).convert("L")
        self.assertAlmostEqual(out.getpixel((0, 0)), 128, delta=2)

    def test_huge_image_is_refused_before_decoding(self):
        data = enc(Image.new("1", (300, 300)), "PNG")
        with mock.patch.object(C, "MAX_PIXELS", 1000):
            with self.assertRaisesRegex(ValueError, "megapixels"):
                C.image_convert(data, "png", "jpg")


class Pdfs(unittest.TestCase):
    def pdf(self, pages=1, size=(200, 200)) -> bytes:
        doc = pymupdf.open()
        for _ in range(pages):
            doc.new_page(width=size[0], height=size[1])
        return doc.tobytes()

    def test_page_cap_on_zip(self):
        with mock.patch.object(C, "MAX_PDF_PAGES", 2):
            with self.assertRaisesRegex(ValueError, "3 pages"):
                C.pdf_to_zip(self.pdf(3))

    def test_zip_output_is_capped_while_building(self):
        with mock.patch.object(C, "MAX_BYTES", 100):
            with self.assertRaisesRegex(ValueError, "limit"):
                C.pdf_to_zip(self.pdf(3))

    def test_huge_page_is_rendered_at_lower_dpi(self):
        w, h = size_of(C.pdf_to_png(self.pdf(1, (14400, 14400))))
        self.assertLessEqual(w * h, C.MAX_RENDER_PIXELS * 1.05)

    def test_final_result_over_the_limit_is_refused(self):
        conv = C.Conversion("pdf", "txt", "text/plain", lambda d: b"x" * 50)
        with mock.patch.dict(C.REGISTRY, {("pdf", "txt"): conv}), mock.patch.object(C, "MAX_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "limit"):
                C.run_chain(b"x", [("pdf", "txt")])


class LibreOffice(unittest.TestCase):
    def test_timeout_kills_the_whole_process_group(self):
        killed = {}
        proc = mock.Mock(pid=4242)
        proc.communicate.side_effect = [__import__("subprocess").TimeoutExpired("soffice", 1), (b"", b"")]
        with mock.patch.object(C.subprocess, "Popen", return_value=proc) as popen, \
                mock.patch.object(C.os, "killpg", side_effect=lambda *a: killed.update(args=a)):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                C.soffice_convert(b"x", "txt", "pdf")
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(killed["args"][0], 4242)


class SaveOutput(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_never_overwrites_and_leaves_no_temp_files(self):
        (self.dir / "a.jpg").write_bytes(b"mine")
        out = C.save_output(self.dir, "a", "jpg", b"new")
        self.assertEqual(out.name, "a-1.jpg")
        self.assertEqual((self.dir / "a.jpg").read_bytes(), b"mine")
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["a-1.jpg", "a.jpg"])

    def test_planted_symlink_temp_name_is_not_followed(self):
        victim = self.dir / "victim.txt"
        victim.write_text("keep")
        (self.dir / ".a.jpg.tmp").symlink_to(victim)  # the old predictable temp name
        C.save_output(self.dir, "a", "jpg", b"data")
        self.assertEqual(victim.read_text(), "keep")

    def test_falls_back_when_hard_links_are_not_supported(self):
        with mock.patch.object(C.os, "link", side_effect=PermissionError):
            out = C.save_output(self.dir, "a", "jpg", b"data")
        self.assertEqual(out.read_bytes(), b"data")


class Symlinks(unittest.TestCase):
    def test_symlinks_are_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            real, link = Path(td) / "real.txt", Path(td) / "link.txt"
            real.write_text("x")
            link.symlink_to(real)
            self.assertFalse(watcher.ignored(real))
            self.assertTrue(watcher.ignored(link))


class WatcherResilience(unittest.TestCase):
    def test_unwritable_out_does_not_kill_the_watcher(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inbox = root / "to-jpg"
            inbox.mkdir()
            (inbox / "out").write_text("a stray file where out/ should be")
            (inbox / "a.png").write_bytes(enc(Image.new("RGB", (8, 8)), "PNG"))
            w = watcher.Watcher(root)
            w.poll()
            w.poll()  # must not raise
            w.poll()
            self.assertTrue((inbox / "failed" / "a.png.err").exists())
            self.assertTrue((inbox / "a.png").exists())


class AppHardening(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        import app as A
        self.A = A
        self.c = TestClient(A.app, base_url="http://127.0.0.1:8420")

    def post(self, headers):
        png = enc(Image.new("RGB", (8, 8)), "PNG")
        return self.c.post("/api/convert?fmt_from=png&fmt_to=jpg", files={"file": ("a.png", png)}, headers=headers)

    def test_other_localhost_port_is_cross_site(self):
        self.assertEqual(self.post({"Origin": "http://localhost:3000"}).status_code, 403)
        self.assertEqual(self.post({"Origin": "https://localhost:8420"}).status_code, 403)

    def test_own_origin_and_no_origin_pass(self):
        for h in ({"Origin": "http://127.0.0.1:8420"}, {"Origin": "http://localhost:8420"}, {}):
            self.assertEqual(self.post(h).status_code, 200, h)


class PdfWorker(unittest.TestCase):
    def pdf(self) -> bytes:
        doc = pymupdf.open()
        doc.new_page()
        return doc.tobytes()

    def test_runs_in_a_child_process_and_returns_bytes(self):
        self.assertTrue(C.REGISTRY[("pdf", "png")].run(self.pdf()).startswith(b"\x89PNG"))

    def test_timeout_stops_the_worker(self):
        with self.assertRaisesRegex(RuntimeError, "took longer"):
            C.run_isolated("pdf_to_zip", self.pdf(), timeout=0.01)

    def test_a_clean_error_keeps_its_type_and_message(self):
        with self.assertRaises(RuntimeError):
            C.REGISTRY[("pdf", "txt")].run(b"not a pdf")

    def test_worker_crash_is_reported_not_raised_raw(self):
        with mock.patch.object(C, "_WORKER_CODE", "import os; os._exit(9)"):
            with self.assertRaisesRegex(RuntimeError, "crashed"):
                C.run_isolated("pdf_to_txt", self.pdf())


@unittest.skipUnless(C.NO_NETWORK, "network isolation (unshare) is not available here")
class LibreOfficeNetwork(unittest.TestCase):
    def test_linked_stylesheet_is_not_fetched(self):
        import http.server
        import threading
        hits = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(404)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        html = f'<html><body><p>hi</p><link rel=stylesheet href="http://127.0.0.1:{srv.server_port}/x.css"></body></html>'
        try:
            self.assertTrue(C.soffice_convert(html.encode(), "html", "pdf").startswith(b"%PDF"))
        finally:
            srv.shutdown()
        self.assertEqual(hits, [])


class Notifications(unittest.TestCase):
    def test_markup_is_escaped_and_options_are_terminated(self):
        real = importlib_notify()
        with mock.patch("shutil.which", return_value="/usr/bin/notify-send"), \
                mock.patch("subprocess.run") as run:
            real("<b>x</b>", "-bad <a href=x>y</a>")
        args = run.call_args.args[0]
        self.assertEqual(args[:4], ["notify-send", "-a", "FileForge", "--"])
        self.assertEqual(args[4:], ["&lt;b&gt;x&lt;/b&gt;", "-bad &lt;a href=x&gt;y&lt;/a&gt;"])


def importlib_notify():
    import importlib
    real = importlib.reload(watcher).notify  # the module-level stub is gone after reload
    watcher.notify = lambda *a, **k: None    # keep the rest of the suite quiet
    return real


class Tif(unittest.TestCase):
    def test_tif_converts_like_tiff(self):
        data = enc(Image.new("RGB", (8, 8)), "TIFF")
        self.assertEqual(size_of(C.REGISTRY[("tif", "png")].run(data)), (8, 8))
        self.assertEqual(C.find_route("tif", "pdf"), [("tif", "pdf")])
        self.assertEqual(sorted(t for s, t in C.REGISTRY if s == "tif"),
                         sorted(t for s, t in C.REGISTRY if s == "tiff"))


def _run_hosted(code: str, **env) -> str:
    """Run `code` in a fresh interpreter as if on Vercel (the mode is read at import time)."""
    import subprocess
    root = str(Path(__file__).resolve().parent.parent)
    out = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {root!r}); {code}"],
                         capture_output=True, text=True, env={**os.environ, "VERCEL": "1", **env})
    return out.stdout.strip()


class Hosted(unittest.TestCase):
    """A Vercel-style deployment: any host name, same-origin check, 4 MB limit, only runnable pairs."""

    def test_any_host_is_served_and_the_api_reports_limit_and_mode(self):
        out = _run_hosted(
            "from fastapi.testclient import TestClient; import app as A; "
            "r = TestClient(A.app, base_url='https://fileforge.example').get('/api/formats'); "
            "print(r.status_code, r.json()['hosted'], r.json()['max_bytes'] >> 20)")
        self.assertEqual(out, "200 True 4")

    def test_same_origin_passes_and_other_sites_do_not(self):
        out = _run_hosted(
            "from fastapi.testclient import TestClient; import app as A; "
            "c = TestClient(A.app, base_url='https://fileforge.example'); "
            "f = lambda o: c.post('/api/convert?fmt_from=png&fmt_to=jpg', files={'file': ('a.png', b'x')}, "
            "headers={'Origin': o} if o else {}).status_code; "
            "print([f('https://fileforge.example'), f('https://evil.example'), "
            "f('https://fileforge.example.evil.example'), f(None)])")
        # the refused ones are 403 before any conversion; allowed ones reach the converter (422 on junk bytes)
        self.assertEqual(out, "[422, 403, 403, 422]")

    def test_without_libreoffice_no_office_source_is_offered(self):
        out = _run_hosted("import converters as C; print(sorted({s for s, _ in C.REGISTRY}))", PATH="/nonexistent")
        for office in ("doc", "docx", "txt", "html", "csv", "xlsx", "pptx"):
            self.assertNotIn(f"'{office}'", out)
        self.assertIn("'png'", out)
        self.assertIn("'pdf'", out)

    def test_without_pdf2docx_pdf_to_docx_is_not_offered(self):
        out = _run_hosted("import sys; sys.modules['pdf2docx'] = None; import converters as C; "
                          "print(('pdf', 'docx') in C.REGISTRY, ('pdf', 'txt') in C.REGISTRY)")
        self.assertEqual(out, "False True")

    def test_local_mode_keeps_the_exact_origin_rule(self):
        from fastapi.testclient import TestClient
        import app as A
        c = TestClient(A.app, base_url="http://127.0.0.1:8420")
        png = enc(Image.new("RGB", (8, 8)), "PNG")
        with mock.patch.object(A, "HOSTED", True):  # hosted rule: Origin must equal the Host header
            r = c.post("/api/convert?fmt_from=png&fmt_to=jpg", files={"file": ("a.png", png)},
                       headers={"Origin": "http://localhost:8420"})
            self.assertEqual(r.status_code, 403)


class AppSafety(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        import app as A
        self.A = A
        self.c = TestClient(A.app, base_url="http://127.0.0.1:8420")

    def test_chunked_upload_without_content_length_is_refused(self):
        def body():
            yield b"x" * 10
        r = self.c.post("/api/convert?fmt_from=png&fmt_to=jpg", content=body(),
                        headers={"Content-Type": "multipart/form-data; boundary=b"})
        self.assertEqual(r.status_code, 411)

    def test_just_over_the_size_limit_is_413_not_a_converter_error(self):
        over = b"\0" * (self.A.MAX_BYTES + 1024)
        r = self.c.post("/api/convert?fmt_from=pdf&fmt_to=txt", files={"file": ("a.pdf", over)})
        self.assertEqual(r.status_code, 413)

    def test_security_headers_on_the_page_and_the_api(self):
        for path in ("/", "/api/formats"):
            h = self.c.get(path).headers
            self.assertIn("default-src 'self'", h["content-security-policy"])
            self.assertEqual(h["x-content-type-options"], "nosniff")
            self.assertEqual(h["referrer-policy"], "no-referrer")

    def test_page_has_no_inline_script_the_csp_would_block(self):
        import re
        html = self.c.get("/").text
        self.assertFalse(re.search(r"<script(?![^>]*\bsrc=)", html))
        self.assertNotRegex(html, r"\son\w+=")

    def test_error_text_hides_server_paths_and_is_short(self):
        msg = self.A._public_message(RuntimeError("failed at /tmp/tmpab12/input.html and file:///tmp/x/lo_profile " + "z" * 500))
        self.assertNotIn("/tmp", msg)
        self.assertLessEqual(len(msg), 301)


if __name__ == "__main__":
    unittest.main()
