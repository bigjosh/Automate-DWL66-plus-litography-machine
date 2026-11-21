"""Exercise the production XY gate with an entirely fake browser/machine API.

The production Playwright library is never imported by these tests. OCR setup
and pixel reads are also faked, so executing the script cannot operate hardware.
"""

from ast import literal_eval
from pathlib import Path
import re
import runpy
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import xy_readout
import plate_transform


SCRIPT = Path(__file__).resolve().parents[1] / "real-dwl.py"
OCR_EXECUTABLE = "offline-fake-tesseract.exe"
PROTECTED_ACTIONS = {"focus-button", "set-zero-button", "job-start", "laser-confirm"}


class FakePSL(ModuleType):
    """Only explicitly defined methods are available; none has live effects."""

    def __init__(self, read_results=None, *, center_read_results=None,
                 zero_read_results=None, skip_check=None, margin_changes=None):
        super().__init__("playwrightscriptlib")
        self.events = []
        self.moves = []
        self.reads = []
        self.center_reads = []
        self.move_reads = []
        self.zero_reads = []
        self.pending = {}
        self.pending_read = None
        self.click_point = None
        self.click_settle_seconds = 0
        self.field = None
        self.read_results = read_results or {}
        self.center_read_results = center_read_results or {}
        self.zero_read_results = zero_read_results or {}
        self.margin_changes = margin_changes or {}
        self.skip_check = skip_check

    def logging(self, enabled):
        pass

    def screenshotOnInfo(self, enabled):
        pass

    def alarmOnError(self):
        pass

    def info(self, text, *, point=None):
        self.click_point = point
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
        self.click_settle_seconds = seconds

    def pauseOnInfo(self, enabled):
        pass

    def verifyFrame(self, name, **kwargs):
        self.events.append(("verify", name))
        return name != self.skip_check

    def doubleClick(self, name):
        if self.click_point != name:
            raise AssertionError("Click pause must identify the pending double-click point")
        self.click_point = None
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
        if self.click_point != name:
            raise AssertionError("Click pause must identify the pending click point")
        self.click_point = None
        if not any(event[0] == "click-guard" for event in self.events):
            raise AssertionError("Machine click before CRD popup guard setup")
        if name == "center-button":
            self.pending_read = ("center", 0, 0)
        elif name == "set-zero-button":
            self.pending_read = ("zero", 0, 0)
        elif name == "move-absolute":
            if set(self.pending) != {"x", "y"}:
                raise AssertionError("Both coordinates must be typed before each move")
            self.moves.append((self.pending["x"], self.pending["y"]))
            self.pending_read = ("move", self.pending["x"], self.pending["y"])
            self.pending.clear()
        self.events.append(("click", name))
        self.wait(self.click_settle_seconds)

    def wait(self, seconds):
        self.events.append(("wait", seconds))

    def grabOCRTextLine(self, name, *, tesseract_cmd, review, validator, retrycount,
                        delay, margin, initial_delay=None):
        if not review or not callable(validator):
            raise AssertionError("Production XY reads require review/recovery and position validation")
        if (retrycount, delay, margin) != (5, 5, 500):
            raise AssertionError("Expected five OCR attempts, five-second spacing, and a 500 µm margin")
        if self.pending_read is None:
            raise AssertionError("OCR read requires a fresh center, absolute move or Set zero")
        phase, x, y = self.pending_read
        self.pending_read = None
        phase_reads, results = {
            "center": (self.center_reads, self.center_read_results),
            "move": (self.move_reads, self.read_results),
            "zero": (self.zero_reads, self.zero_read_results),
        }[phase]
        index = len(phase_reads)
        self.reads.append((name, tesseract_cmd))
        phase_reads.append((name, tesseract_cmd))
        self.wait(delay if initial_delay is None else initial_delay)
        self.events.append(("read", phase, index))
        if callable(results):
            result = results(index, x, y)
        else:
            result = results.get(index, f"X={x}, Y={y} µm")
            if callable(result):
                result = result(x, y)
        if isinstance(result, BaseException):
            raise result
        validator(result, self.margin_changes.get((phase, index), float(margin)))
        return result


class RealDWLXYGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.calibration = Path(temporary.name) / 'plate-calibration.json'
        self.run_list = self.calibration.with_name("run-list.txt")
        self.skip_list = self.calibration.with_name("skip-list.txt")
        self.positions = [(5, 0), (0, 1), (2, 4)]
        self.run_list.write_text("".join(f"({r},{c})\n" for r, c in self.positions),
                                 encoding="utf-8")
        plate_transform.save_calibration(
            self.calibration, plate_transform.RigidTransform(1, 0, 0, 0),
            spacing=28489, calibration_points=[{'row': 0, 'col': 0}, {'row': 5, 'col': 5}])
        # Capture a reference run in deliberately non-grid order using only
        # temporary batch files. The operator's actual batch stays untouched.
        self.reference = FakePSL()
        self.config = self.run_script(self.reference)

    def require_cycles(self, minimum=1):
        if len(self.reference.moves) < minimum:
            self.skipTest(f"Operator configuration enables fewer than {minimum} cycles")

    def run_script(self, fake, *, preflight_error=None, use_default_calibration=False,
                   resume=False, script=SCRIPT):
        def preflight(executable):
            fake.events.append(("preflight", str(executable)))
            if preflight_error is not None:
                raise preflight_error
            return SimpleNamespace(executable=OCR_EXECUTABLE)

        if not resume:
            self.skip_list.unlink(missing_ok=True)
        argv = [str(script), "--layout", "offline-layout",
                "--tesseract-cmd", OCR_EXECUTABLE, "--run-list", str(self.run_list),
                "--skip-list", str(self.skip_list)]
        if not use_default_calibration:
            argv.extend(['--transform-file', str(self.calibration)])
        with patch.dict(sys.modules, {"playwrightscriptlib": fake}), \
                patch("time.sleep", new=fake.wait), \
                patch.object(xy_readout, "TesseractReader", side_effect=preflight), \
                patch.object(sys, "argv", argv):
            return runpy.run_path(str(script), run_name="__main__")

    def recorded_positions(self):
        lines = self.skip_list.read_text(encoding="utf-8").splitlines()
        return [literal_eval(line.strip()) for line in lines
                if line.strip() and not line.lstrip().startswith("#")]

    def test_job_start_alone_leaves_disk_pending(self):
        if not self.config["wetrun"]:
            self.skipTest("Exposure disabled in operator configuration")
        fake = FakePSL()
        verify = fake.verifyFrame

        def interrupt_before_confirm(name, **kwargs):
            if name == "laser-confirm-image":
                raise RuntimeError("Interrupted before exposure confirmation")
            return verify(name, **kwargs)

        with patch.object(fake, "verifyFrame", side_effect=interrupt_before_confirm):
            with self.assertRaisesRegex(RuntimeError, "before exposure confirmation"):
                self.run_script(fake)
        self.assertIn(("click", "job-start"), fake.events)
        self.assertNotIn(("click", "laser-confirm"), fake.events)
        self.assertEqual(self.skip_list.read_text(encoding="utf-8"), "")

    def test_resume_after_confirm_skips_disk_even_if_job_finish_check_failed(self):
        if not self.config["wetrun"]:
            self.skipTest("Exposure disabled in operator configuration")
        self.skip_list.write_text("(0,1)\n", encoding="utf-8")
        fake = FakePSL()
        verify = fake.verifyFrame

        def interrupt_after_confirm(name, **kwargs):
            if name == "job-finished":
                raise RuntimeError("Interrupted before exposure finished")
            return verify(name, **kwargs)

        with patch.object(fake, "verifyFrame", side_effect=interrupt_after_confirm):
            with self.assertRaisesRegex(RuntimeError, "before exposure finished"):
                self.run_script(fake, resume=True)
        self.assertIn(("click", "laser-confirm"), fake.events)
        self.assertEqual(self.recorded_positions(), [(0, 1), (5, 0)])

        resumed = FakePSL()
        self.run_script(resumed, resume=True)
        self.assertEqual(resumed.moves, self.reference.moves[2:])
        self.assertEqual(self.recorded_positions(), [(0, 1), (5, 0), (2, 4)])

        done = FakePSL()
        self.run_script(done, resume=True)
        self.assertFalse(any(e[0] in {"connect", "click", "type"} for e in done.events))

        self.skip_list.unlink()
        new_batch = FakePSL()
        self.run_script(new_batch, resume=True)
        self.assertEqual(new_batch.moves, self.reference.moves)
        self.assertEqual(self.recorded_positions(), self.positions)

    def test_dry_run_performs_moves_without_recording_exposures(self):
        # Use the same flag the operator would edit, in a temporary script.
        text = SCRIPT.read_text(encoding="utf-8")
        text, count = re.subn(r"(?m)^wetrun\s*=\s*(?:True|False)\b", "wetrun=False", text)
        self.assertEqual(count, 1)
        script = self.run_list.with_name("dry-run.py")
        script.write_text(text, encoding="utf-8")
        fake = FakePSL()
        self.run_script(fake, script=script)
        self.assertEqual(fake.moves, self.reference.moves)
        self.assertNotIn(("click", "laser-confirm"), fake.events)
        self.assertEqual(self.skip_list.read_text(encoding="utf-8"), "")

    def test_default_calibration_is_loaded_once_from_beside_script(self):
        # Redirect the read to our fixture, while checking the actual default path.
        fixture = plate_transform.load_plate_calibration(self.calibration)
        with patch.object(plate_transform, 'load_plate_calibration', return_value=fixture) as load:
            self.run_script(FakePSL(), use_default_calibration=True)
        self.assertEqual(load.call_count, 1)
        self.assertEqual(Path(load.call_args.args[0]), SCRIPT.with_name('plate-calibration.json'))

    def test_calibrated_moves_log_local_and_stage_targets_and_verify_stage_readback(self):
        # A 90-degree rotation, non-unit scale, fractional offset and odd pitch
        # distinguish a proper calibrated command from early rounding or truncation.
        spacing = 1001
        plate_transform.save_calibration(
            self.calibration, plate_transform.SimilarityTransform(0, 1, 13.3, -19.25, 1.25),
            spacing=spacing, calibration_points=[{'row': 0, 'col': 0}, {'row': 5, 'col': 5}])
        fake = FakePSL()
        self.run_script(fake)
        enabled = self.positions
        self.assertEqual(len(fake.moves), len(enabled))
        move_indices = [i for i, event in enumerate(fake.events) if event == ('click', 'move-absolute')]
        previous_move = -1
        pair = r'X=([+-]?[0-9.]+),\s*Y=([+-]?[0-9.]+)'
        for (r, c), actual, move_index in zip(enabled, fake.moves, move_indices):
            local = ((r - 2.5) * spacing, -(c - 2.5) * spacing)
            stage = (-1.25 * local[1] + 13.3, 1.25 * local[0] - 19.25)
            self.assertEqual(actual, tuple(round(value) for value in stage))
            logged = [e[1] for e in fake.events[previous_move + 1:move_index] if e[0] == 'info']
            local_log = next(i for i, line in enumerate(logged) if line.startswith('Local target'))
            stage_log = next(i for i, line in enumerate(logged) if line.startswith('Stage target'))
            self.assertLess(local_log, stage_log)
            self.assertEqual(tuple(map(float, re.search(pair, logged[local_log]).groups())), local)
            reported = re.findall(pair, logged[stage_log])
            self.assertEqual(tuple(map(float, reported[0])), stage)
            self.assertEqual(tuple(map(int, reported[1])), actual)
            previous_move = move_index
        self.assertEqual(len(fake.move_reads), len(enabled))

    def test_missing_or_invalid_calibration_stops_before_connect_or_move(self):
        for content, error in [(None, FileNotFoundError), ('{"version": 99}', ValueError)]:
            with self.subTest(content=content):
                if content is None:
                    self.calibration.unlink()
                else:
                    self.calibration.write_text(content, encoding='utf-8')
                fake = FakePSL()
                with self.assertRaises(error):
                    self.run_script(fake)
                self.assertFalse(any(e[0] in {'connect', 'layout', 'click', 'field', 'type', 'read'}
                                     for e in fake.events))

    def test_every_unskipped_move_gets_fresh_read_after_wait_before_focus(self):
        self.require_cycles()
        # All offsets are exercised even when only one cell is enabled.
        for dx, dy in ((0, 0), (500, 0), (-500, 0), (0, 500), (0, -500), (500, -500), (-500, 500)):
            with self.subTest(dx=dx, dy=dy):
                def offset_readout(index, x, y):
                    return f"X={x + dx}, Y={y + dy} µm"

                fake = FakePSL(offset_readout, center_read_results=offset_readout,
                               zero_read_results=offset_readout)
                self.run_script(fake)

                self.assertEqual(fake.moves, self.reference.moves)
                self.assertEqual(fake.reads, [("actual-xy", OCR_EXECUTABLE)] * (3 * len(fake.moves)))
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
                    self.assertEqual([e[1] for e in cycle if e[0] == "read"], ["move", "zero"])
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

    def test_each_disk_focuses_once_and_verifies_its_exposure_origin(self):
        self.require_cycles()
        events = self.reference.events
        legacy_clicks = {"alignment-tab", "near-execute", "alignment-execute",
                         "pre-click-alignment-confirm", "click-alignment-confirm"}
        self.assertFalse(any(e[0] == "click" and e[1] in legacy_clicks for e in events))
        starts = [i for i, event in enumerate(events) if event == ("click", "center-button")]
        self.assertEqual(len(starts), len(self.reference.moves))
        resets = [i for i, event in enumerate(events) if event == ("verify", "center-reset-options")]
        self.assertEqual(len(resets), len(starts))
        for index, (start, end) in enumerate(zip(starts, starts[1:] + [len(events)])):
            self.assertLess(resets[index], start)
            if index:
                self.assertLess(events.index(("read", "zero", index - 1)), resets[index])
            cycle = events[start:end]
            sequence = [
                ("read", "center", index), ("click", "move-absolute"), ("read", "move", index),
                ("click", "focus-button"), ("verify", "focus-executing"), ("verify", "focus-ready"),
                ("verify", "set-zero-button-image"), ("click", "set-zero-button"),
                ("read", "zero", index), ("click", "job-tab"),
            ]
            if self.config["wetrun"]:
                sequence.extend([("click", "job-start"), ("click", "laser-confirm")])
            observed = [event for event in cycle if event in sequence]
            self.assertEqual(observed, sequence)
            zero_click = cycle.index(("click", "set-zero-button"))
            zero_read = cycle.index(("read", "zero", index))
            before_origin_read = cycle[zero_click + 1:zero_read]
            self.assertEqual(sum(e[1] for e in before_origin_read if e[0] == "wait"), 5)

    def test_unverified_coordinate_reset_options_block_center_and_travel(self):
        self.require_cycles()
        fake = FakePSL(skip_check="center-reset-options")
        with self.assertRaises(RuntimeError):
            self.run_script(fake)
        self.assertIn(("verify", "center-reset-options"), fake.events)
        self.assertFalse(any(e[0] in {"click", "field", "type"} for e in fake.events))

    def test_skipped_focus_or_set_zero_check_stops_before_setting_origin(self):
        self.require_cycles()
        for name in ("focus-executing", "focus-ready", "set-zero-button-image"):
            with self.subTest(skipped=name):
                fake = FakePSL(skip_check=name)
                with self.assertRaises(RuntimeError):
                    self.run_script(fake)
                self.assertIn(("verify", name), fake.events)
                clicks = [e[1] for e in fake.events if e[0] == "click"]
                self.assertEqual(clicks.count("focus-button"), 1)
                self.assertTrue({"set-zero-button", "job-tab", "job-start", "laser-confirm"}.isdisjoint(clicks))

    def test_failed_zero_read_stops_before_exposure_and_next_move(self):
        self.require_cycles()
        for result in ("X=501, Y=0 µm", "X=0, Y=-501 µm", "missing",
                       xy_readout.ReadoutError("Zero OCR failed")):
            with self.subTest(result=result):
                fake = FakePSL(zero_read_results={0: result})
                with self.assertRaises(xy_readout.ReadoutError):
                    self.run_script(fake)
                self.assertEqual(len(fake.moves), 1)
                self.assertIn(("click", "set-zero-button"), fake.events)
                self.assertEqual(fake.events[-1], ("read", "zero", 0))
                self.assertFalse(any(e[0] == "click" and e[1] in {"job-tab", "job-start", "laser-confirm"}
                                     for e in fake.events))

    def test_adjusted_move_margin_applies_to_one_check(self):
        self.require_cycles(2)
        def offset(index, x, y):
            return f"X={x + 501}, Y={y} µm"

        fake = FakePSL(read_results=offset, margin_changes={("move", 0): 501})
        with self.assertRaises(xy_readout.ReadoutError):
            self.run_script(fake)
        self.assertEqual(fake.moves, self.reference.moves[:2])
        self.assertEqual(fake.events.count(("click", "focus-button")), 1)
        self.assertEqual(fake.events[-1], ("read", "move", 1))

    def test_invalid_center_read_prevents_typing_movement_focus_and_exposure(self):
        self.require_cycles()
        for result in ("X=501, Y=0 µm", "X=-501, Y=0 µm",
                       "X=0, Y=501 µm", "X=0, Y=-501 µm", "X= 0, Y=0 µm",
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
            "x above tolerance": lambda x, y: f"X={x + 501}, Y={y} µm",
            "x below tolerance": lambda x, y: f"X={x - 501}, Y={y} µm",
            "y above tolerance": lambda x, y: f"X={x}, Y={y + 501} µm",
            "y below tolerance": lambda x, y: f"X={x}, Y={y - 501} µm",
            "OCR unit does not excuse wrong position": lambda x, y: f"X={x + 501}, Y={y} pum",
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
                self.assertEqual(len(fake.reads), 3 * len(fake.moves))
                for name in PROTECTED_ACTIONS:
                    self.assertEqual(fake.events.count(("click", name)),
                                     self.reference.events.count(("click", name)))
                self.assertIn(("info", "Script finished"), fake.events)

    def test_reported_missed_decimal_passes_its_commanded_position(self):
        target = (-71222, 42734)  # nearest-whole-um command for nominal disk (0,1)
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
                            if any(abs(a - b) > 500 for a, b in
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
