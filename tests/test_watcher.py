import io
import os
import sys
from unittest import mock
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

import watcher  # noqa: E402
from watcher import Watcher, history_lines  # noqa: E402

watcher.notify = lambda *a, **k: None  # no desktop popups during tests


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


class WatcherTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.inbox = self.root / "to-jpg"
        self.inbox.mkdir()
        self.w = Watcher(self.root)

    def tearDown(self):
        self._td.cleanup()

    def scan_twice(self, w=None):
        w = w or self.w
        return w.poll() + w.poll()  # first poll only records size/mtime

    def test_converts_and_keeps_original(self):
        (self.inbox / "a.png").write_bytes(png_bytes())
        self.assertEqual(self.scan_twice(), 1)
        self.assertTrue((self.inbox / "out" / "a.jpg").exists())
        self.assertTrue((self.inbox / "a.png").exists())

    def test_waits_for_file_to_stop_changing(self):
        p = self.inbox / "a.png"
        p.write_bytes(png_bytes()[:20])
        self.assertEqual(self.w.poll(), 0)
        p.write_bytes(png_bytes())
        os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 5_000_000))
        self.assertEqual(self.w.poll(), 0)   # changed since last poll: still waiting
        self.assertEqual(self.w.poll(), 1)

    def test_ignores_partial_download_files(self):
        (self.inbox / "a.png.crdownload").write_bytes(png_bytes())
        (self.inbox / ".hidden.png").write_bytes(png_bytes())
        self.assertEqual(self.scan_twice(), 0)

    def test_failure_writes_err_and_does_not_retry(self):
        (self.inbox / "bad.png").write_bytes(b"not an image")
        self.assertEqual(self.scan_twice(), 0)
        self.assertTrue((self.inbox / "failed" / "bad.png.err").read_text().strip())
        self.assertTrue((self.inbox / "bad.png").exists())
        self.assertEqual(self.w.poll(), 0)
        self.assertEqual(len(list((self.inbox / "failed").iterdir())), 1)

    def test_unsupported_pair_fails_cleanly(self):
        (self.inbox / "notes.xyz").write_bytes(b"data")
        self.scan_twice()
        self.assertIn("cannot convert", (self.inbox / "failed" / "notes.xyz.err").read_text())

    def test_same_name_gets_new_output_name(self):
        (self.inbox / "a.png").write_bytes(png_bytes())
        self.scan_twice()
        (self.inbox / "a.png").write_bytes(png_bytes() + b"\0")  # same name, new content
        self.scan_twice()
        names = sorted(p.name for p in (self.inbox / "out").iterdir())
        self.assertEqual(names, ["a-1.jpg", "a.jpg"])

    def test_two_files_both_convert(self):
        (self.inbox / "a.png").write_bytes(png_bytes())
        (self.inbox / "b.png").write_bytes(png_bytes())
        self.assertEqual(self.scan_twice(), 2)

    def test_restart_does_not_reconvert(self):
        (self.inbox / "a.png").write_bytes(png_bytes())
        self.scan_twice()
        again = Watcher(self.root)
        self.assertEqual(self.scan_twice(again), 0)
        self.assertEqual(len(list((self.inbox / "out").iterdir())), 1)

    def flaky_png_to_jpg(self):
        """png->jpg that fails on the first call and works afterwards."""
        real = watcher.REGISTRY[("png", "jpg")]
        calls = {"n": 0}

        def run(data):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("temporary glitch")
            return real.run(data)
        return mock.patch.dict(watcher.REGISTRY, {("png", "jpg"): type(real)("png", "jpg", real.content_type, run)})

    def test_deleting_err_file_retries(self):
        (self.inbox / "a.png").write_bytes(png_bytes())
        with self.flaky_png_to_jpg():
            self.assertEqual(self.scan_twice(), 0)           # fails once
            err = self.inbox / "failed" / "a.png.err"
            self.assertTrue(err.exists())
            self.assertEqual(self.w.poll(), 0)                # .err still there: no retry
            err.unlink()
            self.assertEqual(self.w.poll(), 1)                # retried and converted
        self.assertTrue((self.inbox / "out" / "a.jpg").exists())
        statuses = [r["status"] for r in watcher.read_log(self.root / "watcher.jsonl")]
        self.assertEqual(statuses, ["failed", "ok"])
        self.assertEqual(self.scan_twice(Watcher(self.root)), 0)  # restart: ok file not redone

    def test_failed_file_stays_failed_across_restart_unless_retry_flag(self):
        (self.inbox / "bad.png").write_bytes(b"not an image")
        self.scan_twice()
        self.assertEqual(self.scan_twice(Watcher(self.root)), 0)
        with mock.patch.object(watcher, "REGISTRY", watcher.REGISTRY):
            again = Watcher(self.root, retry_failed=True)
            again.poll(); again.poll()
        rows = watcher.read_log(self.root / "watcher.jsonl")
        self.assertEqual([r["status"] for r in rows], ["failed", "failed"])  # retried, failed again

    def test_history_lines(self):
        (self.inbox / "a.png").write_bytes(png_bytes())
        (self.inbox / "bad.png").write_bytes(b"nope")
        self.scan_twice()
        lines = history_lines(self.root / "watcher.jsonl", 10)
        self.assertEqual(len(lines), 2)
        self.assertTrue(any("ok" in l and "a.png" in l and "a.jpg" in l for l in lines))
        self.assertTrue(any("failed" in l and "bad.png" in l for l in lines))
        self.assertEqual(len(history_lines(self.root / "watcher.jsonl", 1)), 1)
        self.assertEqual(history_lines(self.root / "missing.jsonl", 5), [])


if __name__ == "__main__":
    unittest.main()
