"""Exercise the production XY gate with an entirely fake browser/machine API.

The production Playwright library is never imported by these tests. OCR setup
and pixel reads are also faked, so executing the script cannot operate hardware.
"""

from pathlib import Path
import runpy
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import xy_readout


SCRIPT = Path(__file__).resolve().parents[1] / "real-dwl.py"
OCR_EXECUTABLE = "offline-fake-tesseract.exe"
PROTECTED_ACTIONS = {"focus-button", "alignment-execute", "job-start", "laser-confirm"}


class FakePSL(ModuleType):
    """Only explicitly defined methods are available; none has live effects."""

    def __init__(self, read_results=None, *, center_read_results=None):
        super().__init__("playwrightscriptlib")
        self.events = []
        self.moves = []
        self.reads = []
        self.center_reads = []
        self.move_reads = []
        self.pending = {}
        self.pending_read = None
        self.field = None
        self.read_results = read_results or {}
        self.center_read_results = center_read_results or {}

    def logging(self, enabled):
        pass

    def screenshotOnInfo(self, enabled):
        pass

    def alarmOnError(self):
        pass

    def info(self, text):
        self.events.append(("info", str(text)))

    def connect(self, url, *, page_hint):
        self.events.append(("connect", url, page_hint))

    def loadLayout(self, path):
        self.events.append(("layout", path))

    def checkViewport(self):
        pass

    def enableClickGuard(self, name, *, timeout, referenceScreenshot=None):
        if name != "crd-popup" or timeout != 30:
            raise AssertionError("Production must configure the CRD popup guard with a 30-second timeout")
        self.events.append(("click-guard", name, timeout, referenceScreenshot))

    def clicksSettleTime(self, seconds):
        pass

    def pauseOnInfo(self, enabled):
        pass

    def verifyFrame(self, name, **kwargs):
        self.events.append(("verify", name))

    def doubleClick(self, name):
        if name not in {"x-field", "y-field"}:
            raise AssertionError(f"Unexpected input field: {name}")
        self.field = name[0]
        self.events.append(("field", self.field))

    def sendkeys(self, text):
        if self.field is not None:
            self.pending[self.field] = int(text)
            self.field = None
        elif text != " ":
            raise AssertionError("Coordinate typed without selecting a field")
        self.events.append(("type", text))

    def click(self, name):
        if not any(event[0] == "click-guard" for event in self.events):
            raise AssertionError("Machine click before CRD popup guard setup")
        if name == "center-button":
            self.pending_read = ("center", 0, 0)
        elif name == "move-absolute":
            if set(self.pending) != {"x", "y"}:
                raise AssertionError("Both coordinates must be typed before each move")
            self.moves.append((self.pending["x"], self.pending["y"]))
            self.pending_read = ("move", self.pending["x"], self.pending["y"])
            self.pending.clear()
        self.events.append(("click", name))

    def wait(self, seconds):
        self.events.append(("wait", seconds))

    def grabOCRTextLine(self, name, *, tesseract_cmd):
        if self.pending_read is None:
            raise AssertionError("OCR read requires a fresh center or absolute move")
        phase, x, y = self.pending_read
        self.pending_read = None
        phase_reads = self.center_reads if phase == "center" else self.move_reads
        index = len(phase_reads)
        self.reads.append((name, tesseract_cmd))
        phase_reads.append((name, tesseract_cmd))
        self.events.append(("read", phase, index))
        results = self.center_read_results if phase == "center" else self.read_results
        if callable(results):
            result = results(index, x, y)
        else:
            result = results.get(index, f"X={x}, Y={y} µm")
            if callable(result):
                result = result(x, y)
        if isinstance(result, BaseException):
            raise result
        return result


class RealDWLXYGateTests(unittest.TestCase):
    def setUp(self):
        # Grid, skiplist and exposure enablement are operator configuration.
        # Record the current sequence with valid readbacks using only this fake
        # API; subsequent cases change OCR results, never the production source.
        self.reference = FakePSL()
        self.run_script(self.reference)

    def require_cycles(self, minimum=1):
        if len(self.reference.moves) < minimum:
            self.skipTest(f"Operator configuration enables fewer than {minimum} cycles")

    def run_script(self, fake, *, preflight_error=None):
        def preflight(executable):
            fake.events.append(("preflight", str(executable)))
            if preflight_error is not None:
                raise preflight_error
            return SimpleNamespace(executable=OCR_EXECUTABLE)

        argv = [str(SCRIPT), "--layout", "offline-layout",
                "--tesseract-cmd", OCR_EXECUTABLE]
        with patch.dict(sys.modules, {"playwrightscriptlib": fake}), \
                patch.object(xy_readout, "TesseractReader", side_effect=preflight), \
                patch.object(sys, "argv", argv):
            return runpy.run_path(str(SCRIPT), run_name="__main__")

    def test_every_unskipped_move_gets_fresh_read_after_wait_before_focus(self):
        self.require_cycles()
        # All offsets are exercised even when only one cell is enabled.
        for dx, dy in ((0, 0), (50, 0), (-50, 0), (0, 50), (0, -50), (50, -50), (-50, 50)):
            with self.subTest(dx=dx, dy=dy):
                fake = FakePSL(
                    lambda index, x, y: f"X={x + dx}, Y={y + dy} µm",
                    center_read_results=lambda index, x, y: f"X={x + dx}, Y={y + dy} µm",
                )
                self.run_script(fake)

                self.assertEqual(fake.moves, self.reference.moves)
                self.assertEqual(fake.reads, [("actual-xy", OCR_EXECUTABLE)] * (2 * len(fake.moves)))
                self.assertEqual(len(fake.center_reads), len(fake.moves))
                self.assertEqual(len(fake.move_reads), len(fake.moves))
                move_indices = [i for i, e in enumerate(fake.events)
                                if e == ("click", "move-absolute")]
                for index, start in enumerate(move_indices):
                    end = next((i for i in range(start + 1, len(fake.events))
                                if fake.events[i] == ("click", "center-button")), len(fake.events))
                    cycle = fake.events[start:end]
                    first_focus = cycle.index(("click", "focus-button"))
                    read_index = cycle.index(("read", "move", index))
                    self.assertLess(cycle.index(("wait", 5)), read_index)
                    self.assertLess(read_index, first_focus)
                    self.assertEqual(sum(e[0] == "read" for e in cycle), 1)
                    self.assertTrue(any(e[0] == "info" and "PASS" in e[1]
                                        for e in cycle[read_index + 1:first_focus]))

                for name in PROTECTED_ACTIONS:
                    self.assertEqual(fake.events.count(("click", name)),
                                     self.reference.events.count(("click", name)))
                self.assertLess(next(i for i, e in enumerate(fake.events) if e[0] == "preflight"),
                                next(i for i, e in enumerate(fake.events) if e[0] == "connect"))

    def test_center_is_read_after_wait_before_typing_or_move(self):
        self.require_cycles()
        for index in range(len(self.reference.moves)):
            center_read = self.reference.events.index(("read", "center", index))
            previous_center = max(i for i, event in enumerate(self.reference.events[:center_read])
                                  if event == ("click", "center-button"))
            before_read = self.reference.events[previous_center:center_read]
            self.assertIn(("wait", 5), before_read)
            self.assertFalse(any(event[0] in {"field", "type"} for event in before_read))
            self.assertNotIn(("click", "move-absolute"), before_read)
            following_move_read = self.reference.events.index(("read", "move", index))
            after_read = self.reference.events[center_read + 1:following_move_read]
            self.assertTrue(any(event[0] == "info" and "PASS" in event[1]
                                for event in after_read))
            self.assertIn(("click", "move-absolute"), after_read)

    def test_invalid_center_read_prevents_typing_movement_focus_and_exposure(self):
        self.require_cycles()
        for result in ("X=51, Y=0 µm", "X=-51, Y=0 µm",
                       "X=0, Y=51 µm", "X=0, Y=-51 µm", "X= 0, Y=0 µm",
                       xy_readout.ReadoutError("Center OCR timed out")):
            with self.subTest(result=result):
                fake = FakePSL(center_read_results={0: result})
                with self.assertRaises(xy_readout.ReadoutError):
                    self.run_script(fake)
                self.assertEqual(fake.moves, [])
                self.assertEqual(len(fake.center_reads), 1)
                self.assertEqual(fake.move_reads, [])
                self.assertFalse(any(event[0] in {"field", "type"} for event in fake.events))
                self.assertFalse(any(event[0] == "click" and event[1] in PROTECTED_ACTIONS
                                     for event in fake.events))
                self.assertNotIn(("info", "Script finished"), fake.events)

    def test_invalid_first_move_read_prevents_focus_alignment_and_exposure(self):
        self.require_cycles()
        cases = {
            "x above tolerance": lambda x, y: f"X={x + 51}, Y={y} µm",
            "x below tolerance": lambda x, y: f"X={x - 51}, Y={y} µm",
            "y above tolerance": lambda x, y: f"X={x}, Y={y + 51} µm",
            "y below tolerance": lambda x, y: f"X={x}, Y={y - 51} µm",
            "OCR unit does not excuse wrong position": lambda x, y: f"X={x + 51}, Y={y} pum",
            "lowercase X": lambda x, y: f"x={x}, Y={y} µm",
            "lowercase Y": lambda x, y: f"X={x}, y={y} µm",
            "space before equals": lambda x, y: f"X ={x}, Y={y} µm",
            "space after equals": lambda x, y: f"X= {x}, Y={y} µm",
            "explicit plus sign": lambda x, y: f"X={x}, Y=+{abs(y)} µm",
            "duplicate X": lambda x, y: f"X={x}, X={x}, Y={y} µm",
            "duplicate Y": lambda x, y: f"X={x}, Y={y}, Y={y} µm",
            "missing ending delimiter": lambda x, y: f"X={x}, Y={y}",
            "damaged digits": lambda x, y: f"X={x}I, Y={y} µm",
        }
        for label, raw_text in cases.items():
            with self.subTest(label=label):
                fake = FakePSL({0: raw_text})
                with self.assertRaises(xy_readout.ReadoutError):
                    self.run_script(fake)
                self.assertEqual(fake.moves, self.reference.moves[:1])
                self.assertEqual(len(fake.center_reads), 1)
                self.assertEqual(len(fake.move_reads), 1)
                self.assertFalse(any(e[0] == "click" and e[1] in PROTECTED_ACTIONS
                                     for e in fake.events))
                self.assertNotIn(("info", "Script finished"), fake.events)

    def test_fractions_and_units_do_not_change_integer_position_gate(self):
        self.require_cycles()
        formats = (
            "X={x}, Y={y} µm", "X={x}.3, Y={y}.9 um",
            "X={x} 3, Y={y}.9 pm", "X={x}, Y={y} pum",
            "X={x}, Y={y} unreadable-unit", "X={x}, Y={y},",
        )
        for format_string in formats:
            with self.subTest(format_string=format_string):
                fake = FakePSL(lambda index, x, y: format_string.format(x=x, y=y))
                self.run_script(fake)
                self.assertEqual(fake.moves, self.reference.moves)
                self.assertEqual(len(fake.reads), 2 * len(fake.moves))
                for name in PROTECTED_ACTIONS:
                    self.assertEqual(fake.events.count(("click", name)),
                                     self.reference.events.count(("click", name)))
                self.assertIn(("info", "Script finished"), fake.events)

    def test_reported_missed_decimal_passes_its_commanded_position(self):
        target = (-71222, 42733)
        if target not in self.reference.moves:
            self.skipTest("Reported command is not enabled by the operator configuration")
        move_index = self.reference.moves.index(target)
        fake = FakePSL({move_index: "X=-71222 3, Y=42732.9 um"})
        self.run_script(fake)
        self.assertEqual(fake.moves, self.reference.moves)
        read_index = fake.events.index(("read", "move", move_index))
        next_focus = fake.events.index(("click", "focus-button"), read_index + 1)
        self.assertTrue(any(event[0] == "info" and "PASS" in event[1]
                            for event in fake.events[read_index + 1:next_focus]))
        self.assertIn(("info", "Script finished"), fake.events)

    def test_ocr_failure_propagates_without_focus_or_exposure(self):
        self.require_cycles()
        for error in (xy_readout.ReadoutError("OCR timed out"),
                      RuntimeError("Screenshot failed")):
            with self.subTest(error=type(error).__name__):
                fake = FakePSL({0: error})
                with self.assertRaises(type(error)) as caught:
                    self.run_script(fake)
                self.assertIs(caught.exception, error)
                self.assertEqual(fake.moves, self.reference.moves[:1])
                self.assertFalse(any(e[0] == "click" and e[1] in PROTECTED_ACTIONS
                                     for e in fake.events))

    def test_previous_success_cannot_authorize_focus_after_failed_next_move(self):
        self.require_cycles(2)
        # Choose an actual configured move far enough from its predecessor to
        # make a stuck stage fail the gate; no particular row order is assumed.
        stale_index = next((index for index in range(1, len(self.reference.moves))
                            if any(abs(a - b) > 50 for a, b in
                                   zip(self.reference.moves[index - 1], self.reference.moves[index]))), None)
        if stale_index is None:
            self.skipTest("Configured moves do not differ by more than the gate tolerance")
        previous_x, previous_y = self.reference.moves[stale_index - 1]
        fake = FakePSL({stale_index: f"X={previous_x}, Y={previous_y} µm"})
        with self.assertRaises(xy_readout.ReadoutError):
            self.run_script(fake)
        self.assertGreaterEqual(len(fake.moves), 2)
        self.assertEqual(fake.moves, self.reference.moves[:stale_index + 1])
        self.assertEqual(len(fake.move_reads), stale_index + 1)
        self.assertEqual(len(fake.center_reads), stale_index + 1)
        reference_index = self.reference.events.index(("read", "move", stale_index))
        self.assertEqual(fake.events, self.reference.events[:reference_index + 1])
        failure_index = fake.events.index(("read", "move", stale_index))
        self.assertFalse(any(e[0] == "click" for e in fake.events[failure_index + 1:]))

    def test_missing_ocr_runtime_fails_before_browser_connection_or_actions(self):
        fake = FakePSL()
        error = xy_readout.ReadoutError("Tesseract unavailable")
        with self.assertRaises(xy_readout.ReadoutError) as caught:
            self.run_script(fake, preflight_error=error)
        self.assertIs(caught.exception, error)
        self.assertFalse(any(e[0] in {"connect", "layout", "click", "field", "type", "read"}
                             for e in fake.events))


if __name__ == "__main__":
    unittest.main()
