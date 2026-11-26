import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf  # noqa: E402
from PIL import Image  # noqa: E402

import cli  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def run(*argv):
    """(exit code, stdout, stderr) of cli.main(argv)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def png(self, name="a.png", data=None):
        p = self.dir / name
        p.write_bytes(data if data is not None else png_bytes())
        return p

    def test_convert_writes_beside_original_and_keeps_it(self):
        src = self.png()
        code, out, err = run("convert", src, "--to", "jpg")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue((self.dir / "a.jpg").read_bytes().startswith(b"\xff\xd8"))
        self.assertEqual(src.read_bytes(), png_bytes())
        self.assertIn("a.png ->", out)

    def test_existing_name_is_never_overwritten(self):
        src = self.png()
        (self.dir / "a.jpg").write_bytes(b"precious")
        run("convert", src, "--to", "jpg")
        self.assertEqual((self.dir / "a.jpg").read_bytes(), b"precious")
        self.assertTrue((self.dir / "a-1.jpg").exists())

    def test_out_folder_and_several_files(self):
        a, b = self.png("a.png"), self.png("b.png")
        dest = self.dir / "results"
        code, _, _ = run("convert", a, b, "--to", "jpg", "--out", dest)
        self.assertEqual(code, 0)
        self.assertEqual(sorted(p.name for p in dest.iterdir()), ["a.jpg", "b.jpg"])

    def test_folder_converts_only_what_can_become_the_format(self):
        self.png("a.png"); self.png("b.png"); self.png(".hidden.png")
        (self.dir / "notes.xyz").write_text("x")
        sub = self.dir / "sub"; sub.mkdir()
        (sub / "c.png").write_bytes(png_bytes())
        code, _, err = run("convert", self.dir, "--to", "jpg")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(sorted(p.name for p in self.dir.glob("*.jpg")), ["a.jpg", "b.jpg"])
        self.assertFalse((sub / "c.jpg").exists())

    def test_recursive_folder_reaches_subfolders(self):
        sub = self.dir / "sub"; sub.mkdir()
        (sub / "c.png").write_bytes(png_bytes())
        code, _, _ = run("convert", self.dir, "--to", "jpg", "-r")
        self.assertEqual(code, 0)
        self.assertTrue((sub / "c.jpg").exists())

    def test_folder_with_nothing_convertible_is_exit_2(self):
        (self.dir / "notes.xyz").write_text("x")
        code, _, err = run("convert", self.dir, "--to", "jpg")
        self.assertEqual(code, 2)
        self.assertIn("no files that can become jpg", err)

    def test_chained_conversion(self):
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "hello")
        pdf = self.dir / "scan.pdf"
        pdf.write_bytes(doc.tobytes())
        code, _, _ = run("convert", pdf, "--to", "jpg")   # pdf -> png -> jpg
        self.assertEqual(code, 0)
        self.assertTrue((self.dir / "scan.jpg").read_bytes().startswith(b"\xff\xd8"))

    def test_unreachable_format_is_exit_2_and_writes_nothing(self):
        src = self.png()
        code, _, err = run("convert", src, "--to", "xyz")
        self.assertEqual(code, 2)
        self.assertIn("cannot convert .png to xyz", err)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["a.png"])

    def test_missing_file_is_exit_2(self):
        code, _, err = run("convert", self.dir / "nope.png", "--to", "jpg")
        self.assertEqual(code, 2)
        self.assertIn("no such file", err)

    def test_corrupt_file_is_exit_1_and_the_rest_still_convert(self):
        bad = self.png("bad.png", b"not an image")
        good = self.png("good.png")
        code, out, err = run("convert", bad, good, "--to", "jpg")
        self.assertEqual(code, 1)
        self.assertIn("bad.png: cannot read this image", err)
        self.assertTrue((self.dir / "good.jpg").exists())
        self.assertFalse((self.dir / "bad.jpg").exists())

    def test_targets_lists_direct_then_chained(self):
        code, out, _ = run("targets", self.png())
        lines = out.splitlines()
        self.assertEqual(code, 0)
        self.assertIn("jpg", lines)
        self.assertIn("pdf", lines)                       # direct since image->pdf exists
        self.assertTrue(any(l.startswith("docx via pdf") for l in lines))
        self.assertLess(lines.index("jpg"), next(i for i, l in enumerate(lines) if " via " in l))

    def test_targets_for_unknown_type_is_exit_2(self):
        code, _, err = run("targets", self.dir / "thing.xyz")
        self.assertEqual(code, 2)
        self.assertIn("cannot convert .xyz", err)

    def test_runs_as_a_script_with_the_right_exit_status(self):
        src = self.png()
        ok = subprocess.run([sys.executable, str(ROOT / "cli.py"), "convert", str(src), "--to", "webp"],
                            capture_output=True, text=True, cwd=self.dir)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        bad = subprocess.run([sys.executable, str(ROOT / "cli.py"), "convert", str(src), "--to", "xyz"],
                             capture_output=True, text=True, cwd=self.dir)
        self.assertEqual(bad.returncode, 2)


if __name__ == "__main__":
    unittest.main()
