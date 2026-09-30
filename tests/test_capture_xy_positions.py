"""Offline checks for the calibration workflow; never connect to a browser."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "capture-xy-positions.py"
SPEC = importlib.util.spec_from_file_location("capture_xy_positions", SCRIPT)
capture = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = capture
SPEC.loader.exec_module(capture)

X_BOX = (2190, 740, 2270, 760)
Y_BOX = (2190, 765, 2270, 785)
X_FIELD = "x-field"
Y_FIELD = "y-field"
MOVE_ABSOLUTE = "move-absolute"
EXPECTED_X = (-71222, -42733, -14244, 14244, 42733, 71222)
EXPECTED_Y = (71222, 42733, 14244, -14244, -42733, -71222)


class FakePSL:
    """Fail immediately if the workflow clicks any unrelated machine control."""

    def __init__(self, events):
        self.events = events
        self.size = (2560, 1463)
        self.layout_size = self.size
        self.position = (0, 0)
        # The requested machine position does not imply clean command fields.
        self.pending = {"x": 8642, "y": -9753}
        self.focused = None
        self.moves = []
        self.shots = 0
        self.boxes = {"actual-x": [X_BOX], "actual-y": [Y_BOX]}
        self.selections = []

    def viewportSize(self):
        return self.size

    def checkViewport(self):
        if self.size != self.layout_size:
            raise RuntimeError("Browser viewport differs from selected layout")

    def layoutViewport(self):
        self.checkViewport()
        return self.layout_size

    def layoutBounds(self, name):
        self.checkViewport()
        self.selections.append(name)
        self.events.append(("select", name))
        return self.boxes[name][0]

    def defineRegion(self, name):
        boxes = self.boxes[name]
        if len(boxes) > 1:
            boxes.pop(0)
        return self.layoutBounds(name)

    def viewportGrab(self):
        self.shots += 1
        self.events.append(("screenshot", self.position))
        return Image.new("RGB", self.size, (self.shots % 256, 90, 150))

    def doubleClick(self, name):
        fields = {X_FIELD: "x", Y_FIELD: "y"}
        if name not in fields:
            raise AssertionError("Unexpected field click: %r" % (name,))
        self.focused = fields[name]
        self.events.append(("field", self.focused))

    def sendkeys(self, text):
        if self.focused is None:
            raise AssertionError("Typed without selecting a field")
        self.pending[self.focused] = int(text)
        self.events.append(("type", self.focused, int(text)))

    def click(self, name):
        if name != MOVE_ABSOLUTE:
            raise AssertionError("Unexpected machine action: %r" % (name,))
        self.position = (self.pending["x"], self.pending["y"])
        self.moves.append(self.position)
        self.events.append(("move", self.position))

    def info(self, message):
        pass


class FakeUI:
    def __init__(self, psl, events, actions=None, before_review=None):
        self.psl = psl
        self.events = events
        self.actions = {key: list(value) for key, value in (actions or {}).items()}
        self.before_review = before_review
        self.reviews = []

    def review_capture(self, crop, axis, value, box, position):
        if self.before_review:
            self.before_review(axis, value)
        record = (axis, value, tuple(box), crop.copy(), self.psl.position)
        self.reviews.append(record)
        choices = self.actions.get((axis, value), [])
        action = choices.pop(0) if choices else "accept"
        self.events.append(("review", axis, value, action))
        return action

    def show_status(self, message):
        pass


class CaptureWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.events = []
        self.psl = FakePSL(self.events)
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.events.append(("wait", seconds))

    def run_workflow(self, ui):
        capture.run_capture(self.psl, ui, self.output, sleep=self.sleep)

    def test_coordinates_preserve_production_truncation_and_filename(self):
        self.assertEqual(capture.SPACING, 28489)
        self.assertEqual(capture.X_VALUES, EXPECTED_X)
        self.assertEqual(capture.Y_VALUES, EXPECTED_Y)
        self.assertEqual(
            capture.capture_filename("x", -71222, X_BOX),
            "capture-immediate-x-71222um-2190,740,2270,760.png",
        )
        self.assertEqual(
            capture.capture_filename("y", 0, Y_BOX),
            "capture-immediate-y0um-2190,765,2270,785.png",
        )

    def test_full_sweep_accepts_each_image_before_next_move_and_keeps_other_axis(self):
        expected_captures = [("x", 0), ("y", 0)]
        expected_captures += [("x", value) for value in EXPECTED_X]
        expected_captures += [("y", value) for value in EXPECTED_Y]

        def before_review(axis, value):
            # The image under review must not already have been committed.
            box = X_BOX if axis == "x" else Y_BOX
            self.assertFalse((self.output / capture.capture_filename(axis, value, box)).exists())

        ui = FakeUI(self.psl, self.events, before_review=before_review)
        self.run_workflow(ui)

        self.assertEqual(self.psl.selections, ["actual-x", "actual-y"])
        self.assertEqual([(r[0], r[1]) for r in ui.reviews], expected_captures)
        expected_moves = [(x, 0) for x in EXPECTED_X]
        expected_moves += [(EXPECTED_X[-1], y) for y in EXPECTED_Y]
        self.assertEqual(self.psl.moves, expected_moves)
        self.assertEqual(self.psl.position, (71222, -71222))
        self.assertEqual(self.sleeps, [5] * 12)
        self.assertEqual(len(list(self.output.glob("*.png"))), 14)

        expected_names = set()
        for axis, value, box, crop, position in ui.reviews:
            expected_box = X_BOX if axis == "x" else Y_BOX
            self.assertEqual(box, expected_box)
            filename = capture.capture_filename(axis, value, box)
            expected_names.add(filename)
            with Image.open(self.output / filename) as saved:
                self.assertEqual(saved.size, crop.size)
                self.assertEqual(saved.convert("RGB").tobytes(), crop.convert("RGB").tobytes())
        self.assertEqual({p.name for p in self.output.glob("*.png")}, expected_names)

        relevant = [e for e in self.events if e[0] in ("move", "wait", "review")]
        self.assertEqual(relevant[:2], [("review", "x", 0, "accept"), ("review", "y", 0, "accept")])
        remaining = relevant[2:]
        for index, (axis, value) in enumerate(expected_captures[2:]):
            self.assertEqual(remaining[index * 3:index * 3 + 3], [
                ("move", expected_moves[index]),
                ("wait", 5),
                ("review", axis, value, "accept"),
            ])

    def test_retry_waits_and_recaptures_without_reissuing_move(self):
        ui = FakeUI(self.psl, self.events, actions={("x", EXPECTED_X[0]): ["retry", "accept"]})
        self.run_workflow(ui)
        self.assertEqual(len(self.psl.moves), 12)
        self.assertEqual(self.sleeps, [5] * 13)
        retried = [r for r in ui.reviews if r[:2] == ("x", EXPECTED_X[0])]
        self.assertEqual(len(retried), 2)
        self.assertNotEqual(retried[0][3].tobytes(), retried[1][3].tobytes())
        path = self.output / capture.capture_filename("x", EXPECTED_X[0], X_BOX)
        with Image.open(path) as saved:
            self.assertEqual(saved.tobytes(), retried[1][3].tobytes())
        retry_index = self.events.index(("review", "x", EXPECTED_X[0], "retry"))
        accept_index = self.events.index(("review", "x", EXPECTED_X[0], "accept"))
        between = self.events[retry_index + 1:accept_index]
        self.assertIn(("wait", 5), between)
        self.assertFalse(any(e[0] in ("field", "type", "move") for e in between))

    def test_stop_at_initial_review_does_not_touch_machine_or_save_image(self):
        ui = FakeUI(self.psl, self.events, actions={("x", 0): ["stop"]})
        with self.assertRaises(capture.CaptureCancelled):
            self.run_workflow(ui)
        self.assertEqual(self.psl.moves, [])
        self.assertFalse(any(e[0] in ("field", "type", "move") for e in self.events))
        self.assertEqual(list(self.output.glob("*.png")), [])
        self.assertEqual(self.sleeps, [])

    def test_stop_after_move_preserves_only_accepted_zero_images(self):
        ui = FakeUI(self.psl, self.events, actions={("x", EXPECTED_X[0]): ["stop"]})
        with self.assertRaises(capture.CaptureCancelled):
            self.run_workflow(ui)
        self.assertEqual(self.psl.moves, [(EXPECTED_X[0], 0)])
        self.assertEqual(self.sleeps, [5])
        self.assertEqual({p.name for p in self.output.glob("*.png")}, {
            capture.capture_filename("x", 0, X_BOX),
            capture.capture_filename("y", 0, Y_BOX),
        })
        stop_index = self.events.index(("review", "x", EXPECTED_X[0], "stop"))
        self.assertFalse(any(e[0] in ("field", "type", "move") for e in self.events[stop_index + 1:]))

    def test_pending_stop_after_typing_is_processed_before_move_absolute(self):
        class StopAfterTypingUI(FakeUI):
            def show_status(self, message):
                if self.psl.pending["x"] == EXPECTED_X[0]:
                    raise capture.CaptureCancelled()

        ui = StopAfterTypingUI(self.psl, self.events)
        with self.assertRaises(capture.CaptureCancelled):
            self.run_workflow(ui)
        self.assertIn(("type", "x", EXPECTED_X[0]), self.events)
        self.assertEqual(self.psl.moves, [])
        self.assertEqual(self.sleeps, [])
        self.assertEqual(len(list(self.output.glob("*.png"))), 2)

    def test_reselect_zero_reuses_new_rectangle_for_entire_axis(self):
        new_box = (2180, 740, 2290, 760)
        ui = FakeUI(self.psl, self.events, actions={("x", 0): ["reselect", "accept"]})
        self.psl.boxes["actual-x"] = [X_BOX, new_box]
        self.run_workflow(ui)
        self.assertEqual(self.psl.selections, ["actual-x", "actual-x", "actual-y"])
        self.assertFalse((self.output / capture.capture_filename("x", 0, X_BOX)).exists())
        for value in (0,) + EXPECTED_X:
            self.assertTrue((self.output / capture.capture_filename("x", value, new_box)).exists())
        self.assertTrue(all(r[2] == new_box for r in ui.reviews if r[0] == "x" and r[1] != 0))

    def test_existing_axis_capture_fails_preflight_without_machine_actions(self):
        existing = self.output / "capture-immediate-x123um-1,2,3,4.png"
        existing.write_bytes(b"existing calibration must survive")
        ui = FakeUI(self.psl, self.events)
        with self.assertRaises(FileExistsError):
            self.run_workflow(ui)
        self.assertEqual(existing.read_bytes(), b"existing calibration must survive")
        self.assertFalse(any(e[0] in ("field", "type", "move") for e in self.events))
        self.assertEqual(self.psl.selections, [])

    def test_wrong_viewport_prevents_all_machine_actions(self):
        self.psl.size = (1280, 720)
        ui = FakeUI(self.psl, self.events)
        with self.assertRaises((ValueError, RuntimeError)):
            self.run_workflow(ui)
        self.assertFalse(any(e[0] in ("field", "type", "move") for e in self.events))
        self.assertEqual(list(self.output.glob("*.png")), [])

    def test_nonlegacy_layout_viewport_is_supported(self):
        self.psl.size = self.psl.layout_size = (3000, 1800)
        self.run_workflow(FakeUI(self.psl, self.events))
        self.assertEqual(len(self.psl.moves), 12)
        self.assertEqual(len(list(self.output.glob("*.png"))), 14)

    def test_viewport_change_after_acceptance_prevents_next_move(self):
        def before_review(axis, value):
            if (axis, value) == ("x", EXPECTED_X[0]):
                self.psl.size = (1280, 720)

        ui = FakeUI(self.psl, self.events, before_review=before_review)
        with self.assertRaises((ValueError, RuntimeError)):
            self.run_workflow(ui)
        self.assertEqual(self.psl.moves, [(EXPECTED_X[0], 0)])
        self.assertEqual(self.sleeps, [5])


if __name__ == "__main__":
    unittest.main()
