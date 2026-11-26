import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy" / "kde"))
import install_servicemenu as sm  # noqa: E402
from converters import REGISTRY  # noqa: E402


class ServiceMenuTest(unittest.TestCase):
    def setUp(self):
        self.text = sm.build_desktop()

    def test_every_listed_action_has_a_valid_id_and_a_section(self):
        listed = re.search(r"^Actions=(.*)$", self.text, re.M).group(1).strip(";").split(";")
        self.assertTrue(listed)
        for a in listed:
            self.assertRegex(a, r"^[A-Za-z0-9-]+$")  # freedesktop: no underscores
            self.assertIn(f"[Desktop Action {a}]", self.text)

    def test_offers_every_target_and_calls_cli_with_notify(self):
        for t in {t for _, t in REGISTRY}:
            self.assertIn(f"convert %F --to {t} --notify", self.text)

    def test_heic_is_matched(self):
        self.assertIn("image/heic", sm.source_mimes())


if __name__ == "__main__":
    unittest.main()
