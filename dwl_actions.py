"""Recorded DWL actions shared by plate calibration and batch exposure."""

from decimal import Decimal
import math
from pathlib import Path

from xy_readout import (POSITION_TOLERANCE_UM, ReadoutError, compare_xy_position,
                        format_xy_difference, parse_xy, xy_difference)


LAYOUT_DIR = Path(__file__).resolve().parent / "layout"
MOVE_TOLERANCE_UM = 50
CLICK_SETTLE_SECONDS = 1


def configure_machine_screen(psl, crd_popup_screenshot=None):
    """Bind the connected browser to the recorded DWL layout and input guards."""
    psl.loadLayout(LAYOUT_DIR)
    psl.checkViewport()
    psl.enableClickGuard("crd-popup", timeout=30, referenceScreenshot=crd_popup_screenshot)
    psl.clicksSettleTime(CLICK_SETTLE_SECONDS)
    psl.pauseOnInfo(True)


def check_frame(psl, name, match_level, **options):
    psl.info(f"Check {name}")
    matched = psl.verifyFrame(name, matchLevel=match_level, **options)
    if matched is not True:
        raise RuntimeError(f"Required check {name!r} was skipped or aborted.")


def click(psl, name, message=None, *, double=False):
    psl.info(message or f"Click {name}", point=name)
    (psl.doubleClick if double else psl.click)(name)


def check_stage_position(psl, expected, tesseract_cmd, description="X/Y PASS", *, initial_delay=None):
    """Verify the displayed integer coordinates against the requested stage position."""
    comparison = ""

    def validate(text, margin):
        nonlocal comparison
        _, comparison = compare_xy_position(text, *expected, tolerance=margin)
        return comparison

    text = psl.grabOCRTextLine(
        "actual-xy", tesseract_cmd=tesseract_cmd, review=True, retrycount=5, delay=5,
        initial_delay=initial_delay, margin=POSITION_TOLERANCE_UM, validator=validate)
    psl.info(f"{description}: OCR={text!r}; {comparison}")


def restore_stage_frame(psl, tesseract_cmd):
    """Restore and verify the stage coordinate frame in which calibration is measured."""
    check_frame(psl, "z-zero", 0.999)
    check_frame(psl, "z-zero-check", 0.99)
    check_frame(psl, "center-reset-options", 1.0,
                message="Expected both CENTER reset options to be checked")
    click(psl, "center-button")
    psl.wait(5)
    check_stage_position(psl, (0, 0), tesseract_cmd)


def send_absolute_move(psl, command):
    """Enter a rounded X/Y command, send the move, and wait for it to settle."""
    x, y = command
    click(psl, "global-alignment-tab")
    psl.wait(1)
    for axis, value in (("x", x), ("y", y)):
        check_frame(psl, f"check{axis}-field-label", 0.99)
        click(psl, f"{axis}-field", double=True)
        psl.info(f"Type {axis.upper()}={value} um")
        psl.sendkeys(str(value))
    click(psl, "move-absolute", f"Move absolute to X={x}, Y={y} um")
    psl.wait(5)


def focus(psl, message="Focus on this disk"):
    click(psl, "focus-button", message)
    check_frame(psl, "focus-ready", 0.96, delay=10, retrycount=24)


def raise_head(psl, message="Raise the head after measuring this disk"):
    click(psl, "laser-up-click", message)
    check_frame(psl, "z-zero", 0.999, delay=10, retrycount=78)


def optical_center_find(psl):
    """Run the selected optical center find on an already focused disk."""
    click(psl, "alignment-tab")
    check_frame(psl, "alignment-FindWafterCenterOptical", 0.999)
    # check_frame(psl, "alignment-check-setzero-is-false", 0.999)
    check_frame(psl, "alignment-execute-image", 0.999)
    click(psl, "alignment-execute")
    psl.wait(1)
    check_frame(psl, "check-alignment-confirm", 0.99, delay=10, retrycount=24)
    click(psl, "click-alignment-confirm", "Confirm Find wafer center optical")
    psl.wait(1)
    check_frame(psl, "alignment-button-popped", 0.9, delay=10, retrycount=10)
    psl.wait(2)


def read_xy(psl, tesseract_cmd, *, expected=None, center_near=None,
            max_center_shift=None):
    """Return the full OCR line and validated decimal stage coordinates in µm."""
    def validate(text, margin=None):
        values = parse_xy(text)
        if not all(math.isfinite(float(value)) for value in values):
            raise ReadoutError("X/Y readout is outside the finite numeric range.")
        details = []
        if expected is not None:
            per_axis_limit = Decimal(str(margin))
            dx, dy = xy_difference(values, expected)
            move_diff = f"diff (read - commanded) {format_xy_difference(dx, dy)}"
            if dx.copy_abs() > per_axis_limit or dy.copy_abs() > per_axis_limit:
                raise ReadoutError(
                    f"Move check failed: read X={values[0]}, Y={values[1]} um; "
                    f"commanded X={expected[0]}, Y={expected[1]} um; {move_diff}; "
                    f"must be within +/-{per_axis_limit} um on each axis.")
            details.append(move_diff)
        if center_near is not None:
            shift = math.dist(tuple(map(float, values)), center_near)
            dx, dy = xy_difference(values, center_near)
            details.append(f"diff (found center - commanded) {format_xy_difference(dx, dy)}; "
                           f"radial distance {shift:.1f} um")
            radial_limit = margin if expected is None else max_center_shift
            if shift >= radial_limit:
                raise ReadoutError(
                    f"Center check failed: {'; '.join(details)} "
                    f"(radial distance must be less than {radial_limit} um). Check that "
                    "center finding did not zero/change the coordinates and that "
                    "the intended disk was found.")
        return "; ".join(details)

    text = psl.grabOCRTextLine(
        "actual-xy", tesseract_cmd=tesseract_cmd, review=True, validator=validate,
        retrycount=5, delay=5, margin=MOVE_TOLERANCE_UM if expected is not None else max_center_shift)
    return text, tuple(map(float, parse_xy(text)))
