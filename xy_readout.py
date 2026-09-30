"""Parse and validate DWL Actual X/Y readouts; never controls the machine.

The generic OCR reader is shared with playwrightscriptlib. Existing callers can
continue importing ReadoutError, TesseractReader, and preprocess here.
"""

from decimal import Decimal, InvalidOperation, localcontext
import re

from playwrightscriptocr import OCRError as ReadoutError
from playwrightscriptocr import TesseractReader, preprocess


# Require digits on both sides of a decimal point. Never repair characters that
# could change a value (for example O/0, a missing sign, or a comma/decimal point).
_NUMBER = r"[+-]?[0-9]+(?:\.[0-9]+)?"
# This fixed DWL display is in micrometers. These are the unit strings observed
# from its pixels with our English OCR model; they are not unit conversions.
_MICROMETER_UNIT = r"(?:µm|um|pm|pum)"
_XY_LINE = re.compile(
    rf"\s*X\s*=\s*({_NUMBER})\s*,\s*Y\s*=\s*({_NUMBER})"
    rf"\s*{_MICROMETER_UNIT}\s*",
)

# Production checks only the integer parts. Find labels separately so a second
# occurrence cannot be hidden by a malformed value or a successful first match.
_INTEGER_LABEL = re.compile(r"(?<!\w)([XY])=")
_INTEGER_VALUE = re.compile(r"-?[0-9]+(?=[.,\s])")


def parse_xy(text: str) -> tuple[Decimal, Decimal]:
    """Parse the complete ``X=<number>, Y=<number> µm`` line, or raise.

    Uppercase X/Y labels, equals signs, comma, and a final unit are mandatory.
    The suffix must be µm or an observed OCR rendering of that symbol: um, pm,
    or pum. All mean micrometers for this DWL display. Whitespace between
    tokens and around the line is flexible. Numbers use ASCII digits,
    an optional ASCII +/- sign, and an optional decimal fraction. No character
    substitutions, missing units, exponent notation, or extra text are allowed.
    """
    if not isinstance(text, str):
        raise ReadoutError("The OCR readout must be text.")
    match = _XY_LINE.fullmatch(text)
    if match is None:
        raise ReadoutError(
            "Expected exactly 'X=<number>, Y=<number> <unit>' with uppercase "
            "X/Y and the unit µm (OCR spellings um, pm, pum also accepted); "
            f"unexpected OCR text: {text!r}"
        )
    return Decimal(match.group(1)), Decimal(match.group(2))


def parse_xy_integers(text: str) -> tuple[Decimal, Decimal]:
    """Extract exactly one X/Y integer prefix with strict labels and endings.

    Require literal uppercase X= and Y=, each immediately followed by an
    optional ASCII minus and one or more ASCII digits. The next character must
    be a decimal point, comma, or whitespace; end-of-string alone is not an
    ending. Fractions and units are ignored, not repaired. Labels may occur in
    either order, but duplicates and labels embedded in words are rejected.

    Return integer-valued Decimals to preserve the caller API and signed zero.
    The complete decimal parser parse_xy() remains available for the tester.
    """
    if not isinstance(text, str):
        raise ReadoutError("The OCR readout must be text.")
    labels = list(_INTEGER_LABEL.finditer(text))
    values = {}
    if sorted(label.group(1) for label in labels) == ["X", "Y"]:
        for label in labels:
            value = _INTEGER_VALUE.match(text, label.end())
            if value is not None:
                values[label.group(1)] = Decimal(value.group())
    if len(values) != 2:
        raise ReadoutError(
            "Expected exactly one 'X=' and one 'Y=', each immediately followed "
            "by an optional '-' and ASCII digits ending at a decimal point, "
            "comma, or whitespace; "
            f"unexpected OCR text: {text!r}"
        )
    return values["X"], values["Y"]


def require_xy_position(text, expected_x, expected_y) -> tuple[Decimal, Decimal]:
    """Return extracted X/Y integers only when both are within +/-50 µm.

    Any parse or position error raises ReadoutError, allowing the production
    script to stop before focusing. Commands must be finite integers. Exactly
    50 µm is accepted independently on either axis. Fractions are discarded
    toward zero; this does not claim to measure the full decimal position.
    """
    actual_x, actual_y = parse_xy_integers(text)
    try:
        target_x, target_y = Decimal(str(expected_x)), Decimal(str(expected_y))
    except (InvalidOperation, ValueError) as exc:
        raise ReadoutError("Expected X/Y positions must be finite integers.") from exc
    if any(not target.is_finite() or target != target.to_integral_value()
           for target in (target_x, target_y)):
        raise ReadoutError("Expected X/Y positions must be finite integers.")

    tolerance = Decimal("50")
    values = (actual_x, actual_y, target_x, target_y, tolerance)
    # Keep subtraction exact even if a caller reduced the Decimal precision.
    highest_place = max(value.adjusted() for value in values)
    lowest_place = min(value.as_tuple().exponent for value in values)
    with localcontext() as context:
        context.prec = max(context.prec, highest_place - lowest_place + 3)
        dx, dy = actual_x - target_x, actual_y - target_y
        if abs(dx) > tolerance or abs(dy) > tolerance:
            raise ReadoutError(
                f"X/Y position check FAILED: expected X={target_x}, Y={target_y} um; "
                f"integer readout X={actual_x}, Y={actual_y} um; "
                f"error X={dx}, Y={dy} um (limit +/-{tolerance} um on EACH axis); "
                f"OCR text: {text!r}"
            )
    return actual_x, actual_y
