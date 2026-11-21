"""Exercise visual single clicks against a temporary layout and a simulated screen."""

from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

import playwrightscriptlayout as layouts
import playwrightscriptlib as psl


class GuardedClickTests(unittest.TestCase):
    name = "job-start"
    size = (80, 50)
    box = (7, 9, 35, 28)
    offset = (3, 8)

    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.metadata = self.folder / (self.name + ".json")
        self.screen = Image.new("RGB", self.size, "white")
        draw = ImageDraw.Draw(self.screen)
        draw.rectangle((7, 9, 34, 27), fill="navy", outline="red")
        draw.line((8, 10, 30, 21), fill="yellow", width=2)
        self.page = Mock()
        self.stub(psl, "_page", self.page)
        self.stub(psl, "_layout", None)
        self.stub(psl, "_click_guard", None)
        self.stub(psl, "_clicks_settle_time", 0)
        self.stub(psl, "_emit")
        self.alarm = self.stub(psl, "alarm", return_value=3)
        self.stub(psl, "_run_folder", return_value=str(self.folder))
        self.stub(psl, "viewportSize", return_value=self.size)
        self.capture = self.stub(psl, "viewportGrab", side_effect=lambda **kw: self.screen.copy())
        self.prompt = self.stub(layouts, "prompt_definition", return_value=True)
        self.pick = self.stub(layouts, "pick_on_image", side_effect=[self.box, self.offset])
        self.confirm = self.stub(layouts, "confirm_reference", return_value=True)
        self.stub(layouts, "_root", side_effect=AssertionError("Unexpected recording dialog"))
        self.layout = psl.loadLayout(self.folder)
        self.layout.check_viewport(self.size)

    def stub(self, target, attr, *args, **kwargs):
        return self.enterContext(patch.object(target, attr, *args, **kwargs))

    def legacy_point(self):
        self.metadata.write_text(json.dumps({
            "version": 1, "kind": "point", "x": 12, "y": 16,
        }), encoding="utf-8")

    def load_target(self):
        return json.loads(self.metadata.read_text(encoding="utf-8"))

    def define(self):
        return self.layout.resolve_click(self.name, lambda: self.screen.copy())

    def assert_no_input(self):
        self.assertEqual(self.page.mouse.method_calls, [])
        self.assertEqual(self.page.keyboard.method_calls, [])

    def test_first_click_records_clean_reference_and_reuses_it_on_next_click(self):
        first_crop = self.screen.crop(self.box)
        psl.click(self.name)
        target = self.load_target()
        self.assertEqual(target["kind"], "click")
        self.assertEqual(target["box"], list(self.box))
        self.assertEqual(target["offset"], list(self.offset))
        with Image.open(self.folder / target["image"]) as reference:
            self.assertEqual(reference.convert("RGB").tobytes(), first_crop.tobytes())
        self.screen.putpixel((0, 0), (125, 125, 125))  # Outside the reference rectangle.
        psl.click(self.name)
        self.assertEqual([c.args for c in self.page.mouse.click.call_args_list],
                         [(10, 17), (10, 17)])
        self.prompt.assert_called_once()
        self.confirm.assert_called_once()
        self.assertEqual(self.pick.call_count, 2)

    def test_legacy_point_is_upgraded_only_on_acceptance(self):
        self.legacy_point()
        old = self.metadata.read_bytes()
        self.confirm.return_value = False
        with self.assertRaises(layouts.LayoutCancelled):
            psl.click(self.name)
        self.assertEqual(self.metadata.read_bytes(), old)
        self.assertEqual(list(self.folder.glob("*.png")), [])
        self.assert_no_input()
        self.pick.side_effect = [self.box, self.offset]
        self.confirm.return_value = True
        psl.click(self.name)
        self.assertEqual(self.load_target()["kind"], "click")
        self.page.mouse.click.assert_called_once_with(10, 17)

    def test_double_click_on_field_preserves_legacy_point_and_ignores_field_pixels(self):
        self.legacy_point()
        old = self.metadata.read_bytes()
        popup = self.stub(psl, "_wait_for_click_guard")
        psl.doubleClick(self.name)
        self.page.mouse.dblclick.assert_called_once_with(12, 16)
        popup.assert_called_once()
        self.prompt.assert_not_called()
        self.capture.assert_not_called()
        self.assertEqual(self.metadata.read_bytes(), old)

    def test_double_click_can_use_shared_button_location_without_checking_pixels(self):
        self.define()
        (self.folder / self.load_target()["image"]).unlink()
        self.screen.paste("white", self.box)
        psl.doubleClick(self.name)
        self.page.mouse.dblclick.assert_called_once_with(10, 17)
        self.capture.assert_not_called()

    def test_rejected_rerecording_preserves_old_image_and_target(self):
        self.define()
        old = self.metadata.read_bytes()
        pngs = {p.name: p.read_bytes() for p in self.folder.glob("*.png")}
        self.pick.side_effect = [(6, 8, 38, 29), (0, 0)]
        self.confirm.return_value = False
        with self.assertRaises(layouts.LayoutCancelled):
            self.layout.define_click(self.name, lambda: self.screen.copy(), prompt=False)
        self.assertEqual(self.metadata.read_bytes(), old)
        self.assertEqual({p.name: p.read_bytes() for p in self.folder.glob("*.png")}, pngs)
        self.assert_no_input()

    def test_invalid_point_in_persisted_target_prevents_input_and_recording(self):
        self.define()
        target = self.load_target()
        target["offset"] = [self.box[2] - self.box[0], 0]  # Right edge is exclusive.
        self.metadata.write_text(json.dumps(target), encoding="utf-8")
        self.prompt.reset_mock()
        with self.assertRaises(layouts.LayoutError):
            psl.click(self.name)
        self.prompt.assert_not_called()
        self.assert_no_input()

    def test_missing_saved_reference_prevents_input_and_recording(self):
        self.define()
        (self.folder / self.load_target()["image"]).unlink()
        self.prompt.reset_mock()
        with self.assertRaises(layouts.LayoutError):
            psl.click(self.name)
        self.prompt.assert_not_called()
        self.assert_no_input()

    def test_failed_metadata_write_preserves_original_point_without_orphan_png(self):
        self.legacy_point()
        old = self.metadata.read_bytes()
        self.stub(layouts, "_write_json", side_effect=layouts.LayoutError("disk full"))
        with self.assertRaisesRegex(layouts.LayoutError, "disk full"):
            psl.click(self.name)
        self.assertEqual(self.metadata.read_bytes(), old)
        self.assertEqual(list(self.folder.glob("*.png")), [])
        self.assert_no_input()

    def test_uniform_patch_is_rejected_without_overwriting_legacy_point(self):
        self.legacy_point()
        old = self.metadata.read_bytes()
        self.screen.paste("white", self.box)
        with self.assertRaises(layouts.LayoutError):
            psl.click(self.name)
        self.assertEqual(self.metadata.read_bytes(), old)
        self.assertEqual(list(self.folder.glob("*.png")), [])
        self.assert_no_input()

    def test_moved_target_elsewhere_stops_without_entering_dispatch_context(self):
        self.define()
        crop = self.screen.crop(self.box)
        self.screen.paste("white", self.box)
        self.screen.paste(crop, (45, 26))
        entered = []

        @contextmanager
        def record_dispatch():
            entered.append(True)
            yield

        with self.assertRaises(SystemExit):
            psl.click(self.name, dispatch_context=record_dispatch())
        self.assertFalse(entered)
        self.assert_no_input()
        self.alarm.assert_called_once()

    def test_capture_rechecked_before_first_click_if_screen_changed_while_confirming(self):
        def accept_after_change(*args, **kwargs):
            self.assert_no_input()
            self.screen.paste("black", self.box)
            return True

        self.confirm.side_effect = accept_after_change
        with self.assertRaises(SystemExit):
            psl.click(self.name)
        self.alarm.assert_called_once()
        self.assert_no_input()

    def test_retry_only_dispatches_after_operator_restores_screen(self):
        self.define()
        original = self.screen.copy()
        self.screen.paste("black", self.box)
        events = []

        def restore(message, buttons):
            self.assert_no_input()
            self.assertNotIn("Skip", " ".join(buttons))
            self.screen = original
            events.append("restore")
            return buttons.index("Try compare again")

        @contextmanager
        def record_dispatch():
            events.append("enter")
            yield
            events.append("exit")

        self.alarm.side_effect = restore
        self.page.mouse.click.side_effect = lambda x, y: events.append((x, y))
        psl.click(self.name, dispatch_context=record_dispatch())
        self.assertEqual(events, ["restore", "enter", (10, 17), "exit"])
        self.page.mouse.click.assert_called_once_with(10, 17)

    def test_redefine_on_mismatch_rechecks_new_crop_and_clicks_new_offset(self):
        self.define()
        self.screen.paste("black", self.box)
        new_box = (42, 27, 70, 46)
        ImageDraw.Draw(self.screen).rectangle((45, 30, 61, 36), fill="darkgreen")
        self.pick.side_effect = [new_box, (9, 5)]
        self.alarm.side_effect = [2]
        psl.click(self.name)
        self.assertEqual(self.load_target()["box"], list(new_box))
        self.page.mouse.click.assert_called_once_with(51, 32)

    def test_viewing_differences_then_canceling_redefinition_keeps_click_blocked(self):
        self.define()
        original = self.metadata.read_bytes()
        self.screen.paste("white", self.box)
        self.pick.side_effect = [None]
        self.alarm.side_effect = [1, 2, 3]
        differences = self.stub(psl, "_show_frame_differences")
        with self.assertRaises(SystemExit):
            psl.click(self.name)
        differences.assert_called_once()
        self.assertEqual(self.metadata.read_bytes(), original)
        self.assert_no_input()

    def test_changed_page_during_final_capture_blocks_both_pages(self):
        self.define()
        other_page = Mock()

        def switch_page():
            psl._page = other_page
            return self.screen.copy()

        self.capture.side_effect = switch_page
        with self.assertRaises(layouts.LayoutError):
            psl.click(self.name)
        self.assert_no_input()
        other_page.mouse.click.assert_not_called()

    def test_popup_wait_and_target_match_use_the_same_final_clear_capture(self):
        self.define()
        clear = self.screen.copy()
        wrong = clear.copy()
        wrong.paste("black", self.box)
        self.capture.side_effect = [clear, clear, wrong]
        matcher = Mock()
        matcher.locate.side_effect = [(1, 1), None, None]
        psl._click_guard = dict(name="crd-popup", timeout=2, interval=0.005,
                                matcher=matcher, page=self.page, layout=self.layout,
                                viewport=self.size)
        with self.assertRaises(SystemExit):
            psl.click(self.name)
        self.assertEqual(self.capture.call_count, 3)
        self.assertEqual(matcher.locate.call_count, 3)
        self.alarm.assert_called_once()
        self.assert_no_input()


if __name__ == "__main__":
    unittest.main()
