"""Offline parser/OCR wrapper checks; never connect to or operate the machine."""

from decimal import Decimal, localcontext
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import xy_readout


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class ParseXYTests(unittest.TestCase):
    def test_valid_complete_readouts(self):
        cases = {
            "X=100.3, Y=0 µm": ("100.3", "0"),
            "X=-71222.4, Y=+71222.2 µm": ("-71222.4", "71222.2"),
            " \tX = -0.2,\n Y = -14244.03 µm\n\f": ("-0.2", "-14244.03"),
            "X=0, Y=-0 µm": ("0", "-0"),
            "X=3.000, Y=-3.000 µm": ("3.000", "-3.000"),
            "X=71222.4, Y=-14244 µm": ("71222.4", "-14244"),
            "X=-0.2, Y=0 µm": ("-0.2", "0"),
            "X=-200, Y=1999.8 µm": ("-200", "1999.8"),
            "X=+1.5,Y=-2µm": ("1.5", "-2"),
            "X=71222.4, Y=-14244 pm": ("71222.4", "-14244"),
            "X=-0.2, Y=0 um": ("-0.2", "0"),
            "X=-200, Y=1999.8 pum": ("-200", "1999.8"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(xy_readout.parse_xy(raw), tuple(map(Decimal, expected)))

    def test_ambiguous_or_partial_readouts_fail(self):
        cases = [
            "", "X=100.3", "Y=0", "X=100.3, Y=", "X=100.3, Y=-",
            "X=100., Y=0", "X=.3, Y=0", "X=1e2, Y=0", "X=NaN, Y=0",
            "X=Inf, Y=0", "X=1,000, Y=0", "X=100,3, Y=0", "X=0 Y=0",
            "X=0; Y=0", "X=O, Y=0", "X=0, Y=O", "X=--3, Y=0",
            "Actual X=0, Y=0", "X=0, Y=0 Z=0", "X=0, Y=0\nX=0, Y=0",
            "X=0 µm, Y=0 µm", "X=0, Y=0 mm", "X=0, Y=0 ppm",
            "X=- 3, Y=0", "X=12 34, Y=0", "X=０, Y=0",
            "X=0, Y=0.", "X=0, Y=0 µ", "X=0, Y=0 m",
            "X=0, Y=0", "X=1, Y=2 ",
            "x=0, Y=0 µm", "X=0, y=0 µm", "x=0, y=0 µm",
            "X=−0.2, Y=0 µm", "X=0, Y=−2 µm",
            "Y=0, X=0 µm", "X:0, Y=0 µm", "X=0; Y=0 µm",
            "Actual X=0, Y=0 µm", "?X=0, Y=0 µm", "X=0, Y=0 µm!",
            "X=0, Y=0 µm Z=0", "X=0, Y=0 µm\nX=0, Y=0 µm",
            "X=0 µm, Y=0 µm", "X=0, Y=0 mm", "X=0, Y=0 nm",
            "X=0, Y=0 UM", "X=0, Y=0 µM", "X=0, Y=0 µ m",
            "X=0, Y=0 uum", "X=0, Y=0 uM", "X=0, Y=0 μm",
            "X=0, Y=0 p um", "X=0, Y=0 pum extra", "pm X=0, Y=0 µm",
            "X=NaN, Y=0 µm", "X=Infinity, Y=0 µm", "X=1e2, Y=0 µm",
            "X=100., Y=0 µm", "X=.3, Y=0 µm", "X=1,000, Y=0 µm",
            "X=100,3, Y=0 µm", "X=O, Y=0 µm", "X=0, Y=O µm",
            "X=--3, Y=0 µm", "X=- 3, Y=0 µm", "X=12 34, Y=0 µm",
            "X=０, Y=0 µm", "X=0, Y=0 µm\x00", "\u200bX=0, Y=0 µm",
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(xy_readout.ReadoutError):
                    xy_readout.parse_xy(raw)

    def test_nontext_fails(self):
        with self.assertRaises(xy_readout.ReadoutError):
            xy_readout.parse_xy(None)


class ParseXYIntegersTests(unittest.TestCase):
    def test_integer_parts_with_approved_endings(self):
        cases = {
            "X=71222.4, Y=-14244 µm": ("71222", "-14244"),
            "X=-71222 3, Y=42732.9 um": ("-71222", "42732"),
            "X=-0.2, Y=0 um": ("-0", "0"),
            "X=-200, Y=1999.8 pum": ("-200", "1999"),
            "X=12, Y=-34,": ("12", "-34"),
            "X=12. Y=-34.": ("12", "-34"),
            "X=12\tY=-34\n": ("12", "-34"),
            "X=12\u00a0Y=-34\u00a0": ("12", "-34"),
            "Actual (Y=-34.9 mm), (X=12.3 unknown) trailing text": ("12", "-34"),
            "X=00012, Y=-00034 ": ("12", "-34"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                actual = xy_readout.parse_xy_integers(raw)
                self.assertEqual(actual, tuple(map(Decimal, expected)))
                self.assertTrue(all(isinstance(value, Decimal) for value in actual))

    def test_negative_zero_sign_is_preserved(self):
        actual_x, actual_y = xy_readout.parse_xy_integers("X=-0.2, Y=-0 ")
        self.assertTrue(actual_x.is_zero() and actual_x.is_signed())
        self.assertTrue(actual_y.is_zero() and actual_y.is_signed())

    def test_strict_labels_sign_digits_and_terminators(self):
        invalid_x_values = [
            "X=", "X=-", "X=+10", "X=--10", "X=- 10", "X= 10",
            "X=\t10", "X=\n10", "X =10", "X\t=10", "X:10", "x=10",
            "AX=10", "_X=10", "0X=10", "X=−10", "X=１０", "X=١٠",
            "X=.10", "X=NaN", "X=Infinity", "X=1e1", "X=10O", "X=10;",
            "X=10/", "X=10µm", "X=10０", "X=10-", "X=10\x00",
        ]
        for token in invalid_x_values:
            with self.subTest(token=token):
                with self.assertRaises(xy_readout.ReadoutError):
                    xy_readout.parse_xy_integers(f"{token}, Y=-20 ")
        for raw in ("X=10, Y=-20", "Y=-20, X=10", "X=10, y=-20 ",
                    "X=10, MY=-20 ", "X=10, Y= -20 ", "X=10, Y=+20 ",
                    "X=10, Y=-20O ", "X=10, Y=-20; "):
            with self.subTest(raw=raw):
                with self.assertRaises(xy_readout.ReadoutError):
                    xy_readout.parse_xy_integers(raw)

    def test_invalid_ending_cannot_backtrack_to_shorter_integer(self):
        for value in ("100O", "1000;", "100٠", "100e2", "-100O"):
            with self.subTest(value=value):
                with self.assertRaises(xy_readout.ReadoutError):
                    xy_readout.parse_xy_integers(f"X={value}, Y=0 ")

    def test_missing_or_duplicate_labels_fail_including_malformed_values(self):
        for raw in (
            "", "X=10 ", "Y=-20 ",
            "X=10, X=10, Y=-20 ", "X=10, Y=-20, Y=-20 ",
            "X=oops, X=10, Y=-20 ", "X=10, Y=-20, X= ",
            "X=10, Y=oops, Y=-20 ", "X=10, Y=-20, Y=--20 ",
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(xy_readout.ReadoutError):
                    xy_readout.parse_xy_integers(raw)

    def test_nontext_fails(self):
        for raw in (None, b"X=10, Y=-20 ", 10):
            with self.subTest(raw=raw):
                with self.assertRaises(xy_readout.ReadoutError):
                    xy_readout.parse_xy_integers(raw)


class RequiredPositionTests(unittest.TestCase):
    def test_examples_and_fifty_integer_boundaries_pass_on_each_axis(self):
        cases = [
            ("X=71222.4, Y=-14244 µm", "71222", "-14244"),
            ("X=-71222 3, Y=42732.9 um", "-71222", "42733"),
            ("X=-0.2, Y=0 µm", "0", "0"),
            ("X=-200, Y=1999.8 µm", "-200", "2000"),
            ("X=11.9, Y=-21.9 µm", "10", "-20"),
            ("X=9.1, Y=-19.1 µm", "10", "-20"),
            ("X=-1.9, Y=1.9 µm", "0", "0"),
            ("X=1.9, Y=-1.9 µm", "0", "0"),
            ("X=60, Y=-20 µm", "10", "-20"),
            ("X=-40, Y=-20 µm", "10", "-20"),
            ("X=10, Y=-70 µm", "10", "-20"),
            ("X=10, Y=30 µm", "10", "-20"),
            ("X=-50.9, Y=50.9 µm", "0", "0"),
            ("X=50.9, Y=-50.9 µm", "0", "0"),
            ("X=0, Y=0 µm", "10", "-20"),
            ("X=-0, Y=0 µm", "0", "0"),
            ("X=71222.4, Y=-14244 pm", "71222", "-14244"),
            ("X=-0.2, Y=0 um", "0", "0"),
            ("X=-200, Y=1999.8 pum", "-200", "2000"),
            ("X=10, Y=-20 mm extra text", "10.000", Decimal("-2E+1")),
        ]
        for text, expected_x, expected_y in cases:
            with self.subTest(text=text):
                actual = xy_readout.require_xy_position(text, expected_x, expected_y)
                self.assertEqual(actual, xy_readout.parse_xy_integers(text))

    def test_either_axis_outside_tolerance_fails_with_actual_and_expected(self):
        for text in (
            "X=61, Y=-20 µm", "X=-41.9999, Y=-20 µm",
            "X=10, Y=-71 µm", "X=10, Y=31.9999 µm",
            "X=61, Y=-71 µm",
        ):
            with self.subTest(text=text):
                with self.assertRaises(xy_readout.ReadoutError) as raised:
                    xy_readout.require_xy_position(text, "10", "-20")
                message = str(raised.exception)
                self.assertIn("FAILED", message)
                self.assertIn("expected X=10, Y=-20", message)
                self.assertIn("integer readout X=", message)
                self.assertIn("+/-50 um", message)
                self.assertIn(repr(text), message)

    def test_large_integer_differences_remain_exact_under_reduced_decimal_context(self):
        target = 1000000000000000000000000000000000000000000
        for offset in (-51, -50, -1, 0, 1, 50, 51):
            text = f"X={target + offset}, Y={-target - offset} "
            with self.subTest(offset=offset), localcontext() as context:
                context.prec = 1
                if abs(offset) <= 50:
                    self.assertEqual(
                        xy_readout.require_xy_position(text, target, -target),
                        (Decimal(target + offset), Decimal(-target - offset)),
                    )
                else:
                    with self.assertRaises(xy_readout.ReadoutError):
                        xy_readout.require_xy_position(text, target, -target)
                self.assertEqual(context.prec, 1)

    def test_malformed_readout_fails_even_if_numbers_appear_correct(self):
        for text in ("X=10, Y=-20", "X=10O, Y=-20 ", "x=10, Y=-20 µm",
                     "X =10, Y=-20 µm", "X=10, Y=−20 µm",
                     "X=10, Y=-20, X=oops ", None):
            with self.subTest(text=text):
                with self.assertRaises(xy_readout.ReadoutError):
                    xy_readout.require_xy_position(text, "10", "-20")

    def test_invalid_or_fractional_commanded_values_fail(self):
        for expected_x, expected_y in (("NaN", "0"), ("0", "Infinity"),
                                       ("invalid", "0"), ("0", None),
                                       (True, "0"), ("0", "sNaN"),
                                       ("0.2", "0"), ("0", Decimal("-0.2")),
                                       ("1.000000000000000000000000001", "0")):
            with self.subTest(expected=(expected_x, expected_y)):
                with localcontext() as context:
                    context.prec = 1
                    with self.assertRaises(xy_readout.ReadoutError):
                        xy_readout.require_xy_position("X=0, Y=0 µm", expected_x, expected_y)
                    self.assertEqual(context.prec, 1)


class PreprocessTests(unittest.TestCase):
    def test_resizing_border_and_alpha(self):
        source = Image.new("RGBA", (20, 10), (0, 0, 0, 0))
        source.putpixel((5, 5), (0, 0, 0, 255))
        result = xy_readout.preprocess(source)
        self.assertEqual(result.size, (100, 60))
        self.assertEqual(result.mode, "L")
        self.assertEqual(result.getpixel((0, 0)), 255)
        self.assertEqual(result.getpixel((15, 15)), 255)
        self.assertLess(result.getpixel((32, 32)), 80)
        self.assertEqual(source.size, (20, 10))

    def test_empty_image_fails(self):
        with self.assertRaises(xy_readout.ReadoutError):
            xy_readout.preprocess(Image.new("RGB", (0, 10)))


class TesseractTests(unittest.TestCase):
    def setUp(self):
        self.locate = patch("playwrightscriptocr._find_tesseract", return_value="fake-tesseract")
        self.locate.start()
        self.addCleanup(self.locate.stop)

    def make_reader(self, run):
        run.side_effect = [completed("tesseract 5.5.0\n"), completed("List of available languages (1):\neng\n")]
        reader = xy_readout.TesseractReader()
        self.assertEqual([call.args[0][1:] for call in run.call_args_list], [["--version"], ["--list-langs"]])
        run.reset_mock(side_effect=True)
        return reader

    @patch("playwrightscriptocr.subprocess.run")
    def test_preflight_rejects_missing_english(self, run):
        run.side_effect = [completed("tesseract 5.5.0"), completed("List of available languages (1):\nosd\n")]
        with self.assertRaisesRegex(xy_readout.ReadoutError, "English language data"):
            xy_readout.TesseractReader()

    @patch("playwrightscriptocr.subprocess.run", return_value=completed("different program"))
    def test_preflight_rejects_wrong_executable(self, run):
        with self.assertRaisesRegex(xy_readout.ReadoutError, "identify itself"):
            xy_readout.TesseractReader()

    @patch("playwrightscriptocr.subprocess.run")
    def test_preflight_accepts_windows_version_prefix(self, run):
        run.side_effect = [completed("tesseract v5.5.3.20260724\n"), completed("eng\n")]
        xy_readout.TesseractReader()

    @patch("playwrightscriptocr.subprocess.run")
    def test_reader_uses_fresh_pixels_single_line_and_raw_output(self, run):
        reader = self.make_reader(run)
        inputs = []

        def recognize(command, **kwargs):
            self.assertEqual(command[0], "fake-tesseract")
            self.assertEqual(command[2:7], ["stdout", "-l", "eng", "--psm", "7"])
            self.assertIn("load_system_dawg=0", command)
            self.assertIn("load_freq_dawg=0", command)
            self.assertFalse(any("whitelist" in arg for arg in command))
            self.assertEqual(kwargs["timeout"], 15)
            self.assertNotIn("shell", kwargs)
            inputs.append(Path(command[1]))
            with Image.open(inputs[-1]) as prepared:
                self.assertEqual(prepared.size, (420, 100))
            return completed("X=100.3, Y=0 µm\n")

        run.side_effect = recognize
        for _ in range(2):
            self.assertEqual(reader(Image.new("RGB", (100, 20), "white")), "X=100.3, Y=0 µm\n")
        self.assertNotEqual(inputs[0], inputs[1])
        self.assertTrue(all(not path.exists() for path in inputs))

    @patch("playwrightscriptocr.subprocess.run")
    def test_empty_ocr_fails(self, run):
        reader = self.make_reader(run)
        run.return_value = completed("\n\f")
        with self.assertRaisesRegex(xy_readout.ReadoutError, "no text"):
            reader(Image.new("RGB", (100, 20)))

    @patch("playwrightscriptocr.subprocess.run")
    def test_subprocess_failures_are_readout_errors(self, run):
        reader = self.make_reader(run)
        failures = [
            subprocess.TimeoutExpired(["fake-tesseract"], 15),
            OSError("executable unavailable"),
            completed(stderr="bad image", returncode=1),
        ]
        for failure in failures:
            with self.subTest(failure=failure):
                run.side_effect = [failure]
                with self.assertRaises(xy_readout.ReadoutError):
                    reader(Image.new("RGB", (100, 20)))


class ExecutableTests(unittest.TestCase):
    def test_explicit_missing_executable_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "nonexistent-tesseract.exe"
            with self.assertRaisesRegex(xy_readout.ReadoutError, "not found"):
                xy_readout.TesseractReader(missing)


if __name__ == "__main__":
    unittest.main()
