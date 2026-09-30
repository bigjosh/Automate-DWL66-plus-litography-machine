"""Offline workflow checks. Fakes never connect to or control a machine."""

from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "check-xy-tester.py"
SPEC = importlib.util.spec_from_file_location("check_xy_tester", SCRIPT)
tester = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tester
SPEC.loader.exec_module(tester)

import xy_readout


BOX = (2180, 740, 2440, 765)
X_FIELD = "x-field"
Y_FIELD = "y-field"
MOVE_ABSOLUTE = "move-absolute"
EXPECTED_X = (-71222, -42733, -14244, 14244, 42733, 71222)
EXPECTED_Y = (71222, 42733, 14244, -14244, -42733, -71222)
EXPECTED_MOVES = [(x, 0) for x in EXPECTED_X] + [(71222, y) for y in EXPECTED_Y]


class FakePSL:
    """Accept only the two destination fields and the Move absolute button."""

    def __init__(self, events):
        self.events = events
        self.size = (2560, 1463)
        self.layout_size = self.size
        self.position = (0, 0)
        self.pending = {"x": 8642, "y": -9753}
        self.focused = None
        self.moves = []
        self.shots = 0
        self.messages = []
        self.after_type = None
        self.box = BOX
        self.selections = []

    def viewportSize(self):
        self.events.append(("viewport", self.size))
        return self.size

    def checkViewport(self):
        if self.viewportSize() != self.layout_size:
            raise RuntimeError("Browser viewport differs from selected layout")

    def layoutViewport(self):
        self.checkViewport()
        return self.layout_size

    def layoutBounds(self, name):
        self.checkViewport()
        if name != "actual-xy":
            raise AssertionError(f"Unexpected region: {name}")
        self.selections.append(name)
        self.viewportGrab()
        return self.box

    def frameGrab(self, name):
        self.checkViewport()
        if name != "actual-xy":
            raise AssertionError(f"Unexpected region: {name}")
        return self.viewportGrab().crop(self.box)

    def viewportGrab(self):
        self.shots += 1
        self.events.append(("screenshot", self.position, self.shots))
        return Image.new("RGB", self.size, (self.shots, 90, 150))

    def doubleClick(self, name):
        fields = {X_FIELD: "x", Y_FIELD: "y"}
        if name not in fields:
            raise AssertionError(f"Unexpected field click: {name}")
        self.focused = fields[name]
        self.events.append(("field", self.focused))

    def sendkeys(self, text):
        if self.focused is None:
            raise AssertionError("Typed without selecting a destination field")
        self.pending[self.focused] = int(text)
        self.events.append(("type", self.focused, int(text)))
        if self.after_type:
            self.after_type(self.focused)
        self.focused = None

    def click(self, name):
        if name != MOVE_ABSOLUTE:
            raise AssertionError(f"Unexpected machine action: {name}")
        self.position = (self.pending["x"], self.pending["y"])
        self.moves.append(self.position)
        self.events.append(("move", self.position))

    def info(self, message):
        self.messages.append(str(message))
        self.events.append(("print", str(message)))


class FakeUI:
    def __init__(self, psl, events, confirm=True, accept=True,
                 on_confirm=None, on_review=None, on_status=None):
        self.psl = psl
        self.events = events
        self.confirm = confirm
        self.accept = accept
        self.on_confirm = on_confirm
        self.on_review = on_review
        self.on_status = on_status
        self.confirmations = []
        self.reviews = []

    def show_status(self, message):
        self.events.append(("status", str(message)))
        if self.on_status:
            self.on_status()

    def confirm_move(self, axis, index, position):
        record = (axis, index, tuple(position))
        self.confirmations.append(record)
        self.events.append(("confirm", *record))
        if tuple(position) != (self.psl.pending["x"], self.psl.pending["y"]):
            raise AssertionError("Confirmation appeared before both destination fields were set")
        if self.on_confirm:
            self.on_confirm()
        return self.confirm

    def review_result(self, crop, result):
        self.reviews.append((crop.copy(), result.copy()))
        self.events.append(("review", result["axis"], result["index"]))
        if self.on_review:
            self.on_review(crop, result)
        return self.accept


class CheckXYWorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.output = Path(temp.name)
        self.events = []
        self.psl = FakePSL(self.events)
        self.sleeps = []
        self.read_crops = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.events.append(("wait", seconds))

    def reader(self, crop):
        self.read_crops.append(crop.copy())
        self.events.append(("read", self.psl.position))
        x, y = self.psl.position
        return f"X={x}.0, Y={y}.0 µm"

    def run_workflow(self, ui, reader=None):
        return tester.run_tester(
            self.psl, ui, reader or self.reader, self.output, sleep=self.sleep,
        )

    def read_rows(self):
        path = self.output / "results.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def test_production_coordinates_and_three_micron_tolerance(self):
        self.assertEqual(tester.X_VALUES, EXPECTED_X)
        self.assertEqual(tester.Y_VALUES, EXPECTED_Y)
        self.assertEqual(tester.TOLERANCE, Decimal("3"))

    def test_full_sweep_requires_confirm_then_wait_read_log_and_accept(self):
        def before_accept(crop, result):
            # The review must have recoverable evidence even if the operator stops.
            self.assertEqual(len(self.read_rows()), len(ui.reviews))
            self.assertEqual(len(list(self.output.glob("*.png"))), len(ui.reviews))
            self.assertEqual(crop.size, (BOX[2] - BOX[0], BOX[3] - BOX[1]))
            self.assertEqual(crop.getpixel((0, 0)), (len(ui.reviews) + 1, 90, 150))
            # Editing either field while reviewing must not contaminate the next move.
            self.psl.pending.update(x=777, y=-888)

        ui = FakeUI(self.psl, self.events, on_review=before_accept)
        returned = self.run_workflow(ui)

        self.assertEqual(self.psl.selections, ["actual-xy"])
        self.assertEqual(self.psl.moves, EXPECTED_MOVES)
        self.assertEqual(self.sleeps, [5] * 12)
        self.assertEqual(len(returned), 12)
        self.assertEqual(len(self.read_rows()), 12)
        self.assertEqual(len(self.read_crops), 12)
        self.assertTrue(all(row["passed"] is True for row in returned))
        self.assertEqual([(row["axis"], row["index"]) for row in returned],
                         [(axis, index) for axis in ("x", "y") for index in range(6)])

        relevant = [event for event in self.events if event[0] in {
            "field", "type", "confirm", "move", "wait", "screenshot", "read", "review",
        }]
        self.assertEqual(relevant.pop(0), ("screenshot", (0, 0), 1))
        for move_index, position in enumerate(EXPECTED_MOVES):
            axis = "x" if move_index < 6 else "y"
            index = move_index % 6
            expected = [
                ("field", "x"), ("type", "x", position[0]),
                ("field", "y"), ("type", "y", position[1]),
                ("confirm", axis, index, position), ("move", position),
                ("wait", 5), ("screenshot", position, move_index + 2),
                ("read", position), ("review", axis, index),
            ]
            self.assertEqual(relevant[move_index * 10:(move_index + 1) * 10], expected)

        # Process a queued stop and revalidate layout after every human confirmation.
        for event_index, event in enumerate(self.events):
            if event[0] == "confirm":
                move_index = next(i for i in range(event_index + 1, len(self.events))
                                  if self.events[i][0] == "move")
                between = self.events[event_index + 1:move_index]
                self.assertTrue(any(item[0] == "status" for item in between))
                self.assertTrue(any(item == ("viewport", (2560, 1463)) for item in between))
        printed = "\n".join(self.psl.messages)
        self.assertIn("PASS", printed)
        self.assertIn("X=-71222.0, Y=0.0 µm", printed)
        for row, position in zip(returned, EXPECTED_MOVES):
            for key, value in zip(("commanded_x", "commanded_y", "actual_x", "actual_y"),
                                  position + position):
                self.assertEqual(Decimal(str(row[key])), Decimal(value))
            self.assertEqual(Decimal(str(row["error_x"])), 0)
            self.assertEqual(Decimal(str(row["error_y"])), 0)

    def test_fractional_errors_and_inclusive_per_axis_bounds(self):
        offsets = [("0.3", "0", True), ("3", "-3", True), ("-3", "3", True),
                   ("3.0001", "0", False), ("0", "-3.0001", False),
                   ("2.9", "2.9", True)] * 2

        def read_offset(crop):
            dx, dy, expected = offsets[len(self.psl.moves) - 1]
            x = Decimal(self.psl.position[0]) + Decimal(dx)
            y = Decimal(self.psl.position[1]) + Decimal(dy)
            return f"X={x}, Y={y} µm"

        ui = FakeUI(self.psl, self.events)
        rows = self.run_workflow(ui, read_offset)
        self.assertEqual([row["passed"] for row in rows], [entry[2] for entry in offsets])
        for row, (dx, dy, expected) in zip(rows, offsets):
            self.assertEqual(abs(Decimal(str(row["error_x"]))), abs(Decimal(dx)))
            self.assertEqual(abs(Decimal(str(row["error_y"]))), abs(Decimal(dy)))
        self.assertIn("FAIL", "\n".join(self.psl.messages))

    def test_unreadable_text_fails_without_invented_coordinates_and_can_continue(self):
        def reader(crop):
            if len(self.psl.moves) == 1:
                return "X=-71?22, Y=0 µm"
            return self.reader(crop)

        ui = FakeUI(self.psl, self.events)
        rows = self.run_workflow(ui, reader)
        self.assertEqual(rows[0]["raw_text"], "X=-71?22, Y=0 µm")
        self.assertIs(rows[0]["passed"], False)
        self.assertTrue(rows[0]["reason"])
        self.assertTrue(all(rows[0][key] is None for key in
                            ("actual_x", "actual_y", "error_x", "error_y")))
        self.assertEqual(len(ui.reviews), 12)
        self.assertTrue(all(row["passed"] for row in rows[1:]))

    def test_ocr_error_is_logged_and_reviewed_as_failure(self):
        def broken_reader(crop):
            raise xy_readout.ReadoutError("OCR timed out")

        ui = FakeUI(self.psl, self.events, accept=False)
        with self.assertRaises(tester.TesterCancelled):
            self.run_workflow(ui, broken_reader)
        rows = self.read_rows()
        self.assertEqual(len(rows), 1)
        self.assertIs(rows[0]["passed"], False)
        self.assertIn("timed out", rows[0]["reason"])
        self.assertTrue(all(rows[0][key] is None for key in
                            ("actual_x", "actual_y", "error_x", "error_y")))
        self.assertEqual(len(ui.reviews), 1)
        self.assertEqual(len(self.psl.moves), 1)

    def test_operator_declining_move_prevents_move_wait_and_read(self):
        ui = FakeUI(self.psl, self.events, confirm=False)
        with self.assertRaises(tester.TesterCancelled):
            self.run_workflow(ui)
        self.assertEqual(len(ui.confirmations), 1)
        self.assertEqual(self.psl.moves, [])
        self.assertEqual(self.sleeps, [])
        self.assertEqual(self.read_crops, [])
        self.assertEqual(self.read_rows(), [])
        self.assertEqual(list(self.output.glob("*.png")), [])

    def test_operator_stopping_at_review_preserves_result_and_prevents_next_typing(self):
        ui = FakeUI(self.psl, self.events, accept=False)
        with self.assertRaises(tester.TesterCancelled):
            self.run_workflow(ui)
        self.assertEqual(self.psl.moves, [EXPECTED_MOVES[0]])
        self.assertEqual(len(self.read_rows()), 1)
        self.assertEqual(len(list(self.output.glob("*.png"))), 1)
        review_index = next(i for i, event in enumerate(self.events) if event[0] == "review")
        self.assertFalse(any(event[0] in ("field", "type", "move")
                             for event in self.events[review_index + 1:]))

    def test_close_queued_during_confirmation_prevents_move(self):
        confirmed = False

        def on_confirm():
            nonlocal confirmed
            confirmed = True

        def on_status():
            if confirmed:
                raise tester.TesterCancelled()

        ui = FakeUI(self.psl, self.events, on_confirm=on_confirm, on_status=on_status)
        with self.assertRaises(tester.TesterCancelled):
            self.run_workflow(ui)
        self.assertTrue(confirmed)
        self.assertEqual(self.psl.moves, [])

    def test_wrong_initial_viewport_prevents_machine_actions(self):
        self.psl.size = (1280, 720)
        ui = FakeUI(self.psl, self.events)
        with self.assertRaises((ValueError, RuntimeError)):
            self.run_workflow(ui)
        self.assertFalse(any(event[0] in ("field", "type", "move") for event in self.events))

    def test_nonlegacy_layout_viewport_is_supported(self):
        self.psl.size = self.psl.layout_size = (3000, 1800)
        rows = self.run_workflow(FakeUI(self.psl, self.events))
        self.assertEqual(len(rows), 12)
        self.assertTrue(all(row["passed"] for row in rows))

    def test_viewport_change_during_confirmation_prevents_move(self):
        def change_viewport():
            self.psl.size = (1280, 720)

        ui = FakeUI(self.psl, self.events, on_confirm=change_viewport)
        with self.assertRaises((ValueError, RuntimeError)):
            self.run_workflow(ui)
        self.assertEqual(len(ui.confirmations), 1)
        self.assertEqual(self.psl.moves, [])
        self.assertEqual(self.sleeps, [])

    def test_viewport_change_between_fields_prevents_second_click(self):
        def change_after_type(axis):
            self.psl.size = (1280, 720)

        self.psl.after_type = change_after_type
        ui = FakeUI(self.psl, self.events)
        with self.assertRaises((ValueError, RuntimeError)):
            self.run_workflow(ui)
        self.assertEqual(len([event for event in self.events if event[0] == "field"]), 1)
        self.assertEqual(self.psl.moves, [])

    def test_invalid_region_prevents_typing_or_moves(self):
        ui = FakeUI(self.psl, self.events)
        self.psl.box = (200, 200, 100, 250)
        with self.assertRaises(ValueError):
            self.run_workflow(ui)
        self.assertFalse(any(event[0] in ("field", "type", "move") for event in self.events))


if __name__ == "__main__":
    unittest.main()
