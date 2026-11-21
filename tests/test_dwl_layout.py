"""Check the standalone DWL tools' layout selection without opening a browser."""

from contextlib import chdir
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


PROJECT = Path(__file__).resolve().parents[1]


class StandaloneLayoutTests(unittest.TestCase):
    def test_tools_use_the_project_layout_when_launched_elsewhere(self):
        scripts = {
            "calibrate-plate.py": ("TesseractReader",),
            "capture-xy-positions.py": ("CaptureUI",),
            "check-xy-tester.py": ("TesterUI", "TesseractReader"),
        }
        with tempfile.TemporaryDirectory() as folder, chdir(folder):
            for filename, setup_calls in scripts.items():
                with self.subTest(script=filename):
                    script = PROJECT / filename
                    spec = importlib.util.spec_from_file_location(script.stem, script)
                    program = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(program)
                    browser = Mock()
                    browser.loadLayout.side_effect = SystemExit("layout reached")
                    argv = [str(script), "--output-dir", folder]
                    with (
                        patch.dict(sys.modules, {"playwrightscriptlib": browser}),
                        patch.object(sys, "argv", argv),
                        patch.multiple(program, **{name: Mock() for name in setup_calls}),
                        self.assertRaisesRegex(SystemExit, "layout reached"),
                    ):
                        program.main()
                    browser.loadLayout.assert_called_once_with(PROJECT / "layout")
