"""Verify run output locations without a connection to a browser."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import playwrightscriptlib as psl


class RunOutputTests(unittest.TestCase):
    def test_resuming_appends_log_and_keeps_captures_in_selected_folder(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(temporary)
        folder = root / "runs" / "261120 113412"
        image = Image.new("RGB", (12, 12), "blue")
        for name, value in (("_configured_run_dir", None), ("_run_dir", None), ("_log", None),
                            ("_shot_dir", None), ("_page", None), ("_pause_on_info", False)):
            self.enterContext(patch.object(psl, name, value))
        self.enterContext(patch.object(sys, "argv", [str(root / "fake-script.py")]))
        self.enterContext(patch.object(psl, "viewportGrab", return_value=image))
        self.enterContext(patch("builtins.print"))
        self.addCleanup(psl.logging, False)
        for index in range(2):
            psl.setRunFolder(folder)
            psl.logging(True)
            psl.screenshotOnInfo(True)
            with patch.object(psl, "_page", object()):
                psl.info(f"Run attempt {index}")
            psl._save_click_guard_timeout(image)
            self.assertTrue(all(Path(path).parent == folder for path in psl._save_diff_files(image, image)))
            psl.logging(False)
        log = (folder / "fake-script.log").read_text(encoding="utf-8")
        self.assertIn("Run attempt 0", log)
        self.assertIn("Run attempt 1", log)
        self.assertEqual(len(list(folder.glob("*-Run attempt *.png"))), 2)
        self.assertEqual(len(list(folder.glob("click-guard-timeout-*.png"))), 2)
        self.assertEqual(len(list(folder.glob("compare-diff-*.png"))), 6)
        self.assertEqual(list((root / "runs").iterdir()), [folder])
