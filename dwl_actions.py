"""Recorded DWL actions shared by plate calibration and batch exposure."""

from decimal import Decimal
import math
from pathlib import Path

from xy_readout import ReadoutError, parse_xy


LAYOUT_DIR = Path(__file__).resolve().parent / "layout"
MOVE_TOLERANCE_UM = 50


def check_frame(psl, name, match_level, *, required=True, **options):
    psl.info(f"Check {name}")
    matched = psl.verifyFrame(name, matchLevel=match_level, **options)
    if required and matched is not True:
        raise RuntimeError(f"Required check {name!r} was skipped or aborted.")


def click(psl, name, message=None, *, double=False):
    psl.info(message or f"Click {name}", point=name)
    (psl.doubleClick if double else psl.click)(name)


def send_absolute_move(psl, command, *, require_match=True):
    """Enter a rounded X/Y command, send the move, and wait for it to settle."""
    x, y = command
    click(psl, "global-alignment-tab")
    psl.wait(1)
    for axis, value in (("x", x), ("y", y)):
        check_frame(psl, f"check{axis}-field-label", 0.99, required=require_match)
        click(psl, f"{axis}-field", double=True)
        psl.info(f"Type {axis.upper()}={value} um")
        psl.sendkeys(str(value))
    click(psl, "move-absolute", f"Move absolute to X={x}, Y={y} um")
    psl.wait(5)


def focus(psl, message="Focus on this disk"):
    click(psl, "focus-button", message)
    psl.wait(5)
    check_frame(psl, "focus-executing", 0.96, delay=10, retrycount=24)
    check_frame(psl, "focus-ready", 0.96, delay=10, retrycount=24)


def optical_center_find(psl):
    """Run the selected optical center find on an already focused disk."""
    click(psl, "alignment-tab")
    check_frame(psl, "alignment-execute-image", 0.999)
    click(psl, "near-execute", "Focus the alignment panel next to Execute")
    click(psl, "alignment-execute")
    psl.wait(1)
    check_frame(psl, "check-alignment-confirm", 0.99, delay=10, retrycount=24)
    click(psl, "pre-click-alignment-confirm")
    click(psl, "click-alignment-confirm", "Confirm Find wafer center optical")
    psl.wait(1)
    check_frame(psl, "alignment-button-pushed", 0.9, delay=10, retrycount=10)
    check_frame(psl, "alignment-button-popped", 0.9, delay=10, retrycount=10)
    psl.wait(5)


def read_xy(psl, tesseract_cmd, *, expected=None, center_near=None,
            max_center_shift=None):
    """Return the full OCR line and validated decimal stage coordinates in µm."""
    def validate(text, margin=None):
        values = parse_xy(text)
        if not all(math.isfinite(float(value)) for value in values):
            raise ReadoutError("X/Y readout is outside the finite numeric range.")
        if expected is not None:
            per_axis_limit = Decimal(str(margin))
            errors = [abs(value - Decimal(str(target)))
                      for value, target in zip(values, expected)]
            if any(error > per_axis_limit for error in errors):
                raise ReadoutError(
                    f"Move check failed: read {values}, commanded {expected}; "
                    f"must be within +/-{per_axis_limit} um on each axis.")
        if center_near is not None:
            shift = math.dist(tuple(map(float, values)), center_near)
            radial_limit = margin if expected is None else max_center_shift
            if shift >= radial_limit:
                raise ReadoutError(
                    f"Found center is {shift:.1f} um from the commanded position "
                    f"(radial distance must be less than {radial_limit} um). Check that "
                    "center finding did not zero/change the coordinates and that "
                    "the intended disk was found.")

    text = psl.grabOCRTextLine(
        "actual-xy", tesseract_cmd=tesseract_cmd, review=True, validator=validate,
        retrycount=5, delay=5, margin=MOVE_TOLERANCE_UM if expected is not None else max_center_shift)
    return text, tuple(map(float, parse_xy(text)))
