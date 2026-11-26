"""Runs the Node UI tests for static/app.js (tests/js, node:test + a small DOM stub)."""
import shutil
import subprocess
import unittest
from pathlib import Path

JS = Path(__file__).resolve().parent / "js"


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class JsUi(unittest.TestCase):
    def test_node_suite_passes(self):
        files = sorted(str(p) for p in JS.glob("*.test.js"))
        r = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
