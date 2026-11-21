"""Plate-to-stage coordinates for the six-by-six DWL disk tray.

This module performs arithmetic and calibration-file I/O; it never connects to
the machine.
Distances are in micrometers. Similarity fits include uniform scale, rotation,
and translation. Legacy rigid fits keep scale fixed at one.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
import json
import math
import os
from pathlib import Path
import tempfile


VALIDATION_TOLERANCE_UM = 500.0  # Radial error at the independent third disk.
TWO_EXPOSED_ANCHORS = "two_exposed_anchors"


def _finite(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _point(value, label):
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{label} must contain exactly two coordinates")
    try:
        x, y = value
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain exactly two coordinates") from exc
    return _finite(x, f"{label} X"), _finite(y, f"{label} Y")


def plate_position(row, col, spacing=28489.0):
    """Return a nominal disk center, preserving half-micrometer coordinates.

    Rows increase along X; columns increase along negative Y, matching
    real-dwl.py. Indices must be integers in the inclusive range 0 through 5.
    """
    for label, index in (("row", row), ("column", col)):
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= 5:
            raise ValueError(f"{label} must be an integer from 0 through 5")
    spacing = _finite(spacing, "spacing")
    if spacing <= 0:
        raise ValueError("spacing must be positive")
    return _point(((row - 2.5) * spacing, -(col - 2.5) * spacing), "plate position")


def farthest_disks(pending):
    """Pick the longest available baseline, resolving ties in run-list order."""
    positions = tuple(pending)
    if len(positions) < 2:
        return positions
    return max(combinations(positions, 2), key=lambda pair: math.dist(*pair))


@dataclass(frozen=True)
class RigidTransform:
    """Map plate coordinates to stage coordinates: stage = R * plate + t."""

    cos_theta: float
    sin_theta: float
    tx: float
    ty: float

    def __post_init__(self):
        for name in ("cos_theta", "sin_theta", "tx", "ty"):
            object.__setattr__(self, name, _finite(getattr(self, name), name))
        if not math.isclose(math.hypot(self.cos_theta, self.sin_theta), 1.0,
                            rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("Rotation must have unit length; scaling is not supported")

    @property
    def angle_degrees(self):
        return math.degrees(math.atan2(self.sin_theta, self.cos_theta))

    @property
    def scale(self):
        return 1.0

    @property
    def matrix(self):
        """Complete homogeneous matrix acting on column vectors [x, y, 1]."""
        a, b = self.scale * self.cos_theta, self.scale * self.sin_theta
        return [[a, -b, self.tx], [b, a, self.ty], [0.0, 0.0, 1.0]]

    def apply(self, point):
        x, y = _point(point, "plate point")
        a, b = self.scale * self.cos_theta, self.scale * self.sin_theta
        return _point((a * x - b * y + self.tx,
                       b * x + a * y + self.ty),
                      "transformed point")


@dataclass(frozen=True)
class SimilarityTransform(RigidTransform):
    """Map local centers to stage centers: stage = scale * R * plate + t."""

    scale: float = 1.0

    def __post_init__(self):
        super().__post_init__()
        object.__setattr__(self, "scale", _finite(self.scale, "scale"))
        if self.scale <= 0:
            raise ValueError("scale must be positive")


def fit_rigid_transform(plate_a, plate_b, stage_a, stage_b):
    """Fit the angle of two point pairs and align their midpoints.

    Correspondences must be in the same order and each pair must be distinct.
    Unequal pair distances leave equal-and-opposite endpoint residuals rather
    than introducing scale. A third point can independently test the fit.
    """
    plate_a, plate_b = _point(plate_a, "plate A"), _point(plate_b, "plate B")
    stage_a, stage_b = _point(stage_a, "stage A"), _point(stage_b, "stage B")

    directions = []
    for label, a, b in (("plate", plate_a, plate_b), ("stage", stage_a, stage_b)):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if not math.isfinite(length) or length == 0:
            raise ValueError(f"{label} points must have a finite, nonzero separation")
        directions.append((dx / length, dy / length))
    (px, py), (sx, sy) = directions
    cos_theta = px * sx + py * sy
    sin_theta = px * sy - py * sx
    norm = math.hypot(cos_theta, sin_theta)
    cos_theta, sin_theta = cos_theta / norm, sin_theta / norm

    # Halve before adding so finite coordinates do not overflow in the sum.
    px, py = (plate_a[i] / 2 + plate_b[i] / 2 for i in (0, 1))
    sx, sy = (stage_a[i] / 2 + stage_b[i] / 2 for i in (0, 1))
    tx = sx - (cos_theta * px - sin_theta * py)
    ty = sy - (sin_theta * px + cos_theta * py)
    return RigidTransform(cos_theta, sin_theta, tx, ty)


def fit_similarity_transform(plate_a, plate_b, stage_a, stage_b):
    """Fit both point correspondences with uniform scale, rotation and offset.

    Two points cannot determine independent X/Y scales, shear, or local jig
    distortion. Keep a third point independent to measure predictive error.
    """
    plate_a, plate_b = _point(plate_a, "plate A"), _point(plate_b, "plate B")
    stage_a, stage_b = _point(stage_a, "stage A"), _point(stage_b, "stage B")
    rotation = fit_rigid_transform(plate_a, plate_b, stage_a, stage_b)
    scale = _finite(math.dist(stage_a, stage_b) / math.dist(plate_a, plate_b), "scale")
    if scale <= 0:
        raise ValueError("scale must be positive")
    px, py = (plate_a[i] / 2 + plate_b[i] / 2 for i in (0, 1))
    sx, sy = (stage_a[i] / 2 + stage_b[i] / 2 for i in (0, 1))
    a, b = scale * rotation.cos_theta, scale * rotation.sin_theta
    return SimilarityTransform(rotation.cos_theta, rotation.sin_theta,
                               sx - (a * px - b * py),
                               sy - (b * px + a * py), scale)


def _json_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a JSON number")
    return _finite(value, label)


def _json_point(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} must be an array of two numbers")
    return tuple(_json_number(item, label) for item in value)


def _finite_metadata(value):
    """Reject nonfinite numbers even in optional caller-owned metadata."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Calibration must not contain nonfinite numbers")
    if isinstance(value, dict):
        for item in value.values():
            _finite_metadata(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _finite_metadata(item)


def _validate_calibration(document):
    if not isinstance(document, dict):
        raise ValueError("Calibration must be a JSON object")
    _finite_metadata(document)
    version = document.get("version")
    if type(version) is not int or version not in (1, 2):
        raise ValueError("Unsupported calibration version; expected 1 or 2")
    expected_model = "rigid_2d" if version == 1 else "similarity_2d"
    if document.get("model") != expected_model:
        raise ValueError(f"Unsupported model for version {version}; expected {expected_model}")
    if document.get("units") != "um":
        raise ValueError("Unsupported calibration units; expected um")
    if _json_number(document.get("spacing_um"), "spacing_um") <= 0:
        raise ValueError("spacing_um must be positive")
    points = document.get("calibration_points")
    if not isinstance(points, list) or len(points) != 2 or not all(
            isinstance(point, dict) for point in points):
        raise ValueError("calibration_points must contain exactly two measurement objects")
    if document.get("validation") is not None and not isinstance(document["validation"], dict):
        raise ValueError("validation must be an object or null")
    matrix = document.get("rotation_matrix")
    if not isinstance(matrix, list) or len(matrix) != 2:
        raise ValueError("rotation_matrix must be a two-by-two numeric array")
    (a, b), (c, d) = (_json_point(row, "rotation_matrix row") for row in matrix)
    # A proper 2D rotation has [[cos, -sin], [sin, cos]], with unit columns.
    # This excludes scaling, shear, and reflections, including det=-1 matrices.
    if not (math.isclose(a, d, rel_tol=0.0, abs_tol=1e-12)
            and math.isclose(b, -c, rel_tol=0.0, abs_tol=1e-12)):
        raise ValueError("rotation_matrix must be an orthonormal rotation with determinant +1")
    tx, ty = _json_point(document.get("translation_um"), "translation_um")
    if version == 1:
        if "scale" in document and _json_number(document["scale"], "scale") != 1.0:
            raise ValueError("Version-1 rigid calibration cannot contain a non-unit scale")
        transform = RigidTransform(a, c, tx, ty)
    else:
        transform = SimilarityTransform(a, c, tx, ty,
                                        _json_number(document.get("scale"), "scale"))
    if version == 2 or "transform_matrix" in document:
        complete = document.get("transform_matrix")
        if not isinstance(complete, list) or len(complete) != 3 or any(
                not isinstance(row, list) or len(row) != 3 for row in complete):
            raise ValueError("transform_matrix must be a three-by-three numeric array")
        for row, expected_row in zip(complete, transform.matrix):
            for value, expected in zip(row, expected_row):
                value = _json_number(value, "transform_matrix entry")
                if not math.isclose(value, expected, rel_tol=1e-12, abs_tol=1e-12):
                    raise ValueError("transform_matrix disagrees with scale, rotation or translation")
    return transform


def save_calibration(path, transform, *, spacing, calibration_points, validation=None, overwrite=True):
    """Atomically save a rigid (v1) or similarity (v2) calibration.

    ``calibration_points`` contains exactly two caller-defined dictionaries.
    ``validation`` may be omitted/pending while the tester reloads this file
    for an independent third-corner measurement. A finished batch fit uses
    {"method": TWO_EXPOSED_ANCHORS} after both anchor exposures finish.
    Set overwrite=False to publish only if the destination is still absent.
    """
    if not isinstance(transform, RigidTransform):
        raise ValueError("transform must be a RigidTransform or SimilarityTransform")
    similarity = isinstance(transform, SimilarityTransform)
    document = {
        "version": 2 if similarity else 1,
        "model": "similarity_2d" if similarity else "rigid_2d",
        "units": "um",
        "spacing_um": _json_number(spacing, "spacing_um"),
        "rotation_matrix": [[transform.cos_theta, -transform.sin_theta],
                            [transform.sin_theta, transform.cos_theta]],
        "translation_um": [transform.tx, transform.ty],
        "transform_matrix": transform.matrix,
        "calibration_points": calibration_points,
        "validation": validation,
        "saved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if similarity:
        document["scale"] = transform.scale
    _validate_calibration(document)
    if validation and validation.get("method") == TWO_EXPOSED_ANCHORS:
        _verify_measurements(document, transform)
    try:
        serialized = json.dumps(document, indent=2, allow_nan=False) + "\n"
    except (TypeError, ValueError) as exc:
        raise ValueError("Calibration metadata must contain JSON-compatible finite values") from exc
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=f".{path.name}.",
                                         suffix=".tmp", delete=False) as handle:
            temporary_path = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temporary_path, path)
        elif os.name == "nt":
            os.rename(temporary_path, path)  # Windows fails if the destination exists.
        else:
            os.link(temporary_path, path)    # Publish without replacing an existing POSIX file.
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate calibration field: {key}")
        result[key] = value
    return result


def _read_calibration(path):
    with Path(path).open(encoding="utf-8") as handle:
        document = json.load(handle, object_pairs_hook=_unique_object)
    return document, _validate_calibration(document)


def load_plate_calibration(path):
    """Read the nominal grid spacing in µm and the matching transform together."""
    document, transform = _read_calibration(path)
    return float(document["spacing_um"]), transform


def _check_measured_fit(document, transform):
    anchors = document["calibration_points"]
    disks = [(point.get("row"), point.get("col")) for point in anchors]
    nominal = [plate_position(*disk, document["spacing_um"]) for disk in disks]
    for point, grid_position in zip(anchors, nominal):
        if "nominal_xy_um" in point:
            saved = _json_point(point["nominal_xy_um"], "nominal fit center")
            if math.dist(saved, grid_position) > 1e-6:
                raise ValueError("a nominal fit center disagrees with its grid position")
    centers = [_json_point(p.get("located_xy_um"), "measured fit center") for p in anchors]
    fit = fit_similarity_transform if isinstance(transform, SimilarityTransform) else fit_rigid_transform
    original = fit(*nominal, *centers)
    if any(math.dist(transform.apply(xy), original.apply(xy)) > 1e-6 for xy in nominal):
        raise ValueError("the transform disagrees with its two measured fit centers")
    return disks


def _check_third_disk(check, disks, spacing, transform):
    a, b = disks
    third = check.get("row"), check.get("col")
    third_xy = plate_position(*third, spacing)
    if (b[0] - a[0]) * (third[1] - a[1]) == (b[1] - a[1]) * (third[0] - a[0]):
        raise ValueError("the three calibration disks must form a triangle")
    predicted = transform.apply(third_xy)
    recorded = _json_point(check.get("expected_xy_um"), "third-disk prediction")
    if math.dist(predicted, recorded) > 1e-6:
        raise ValueError("the saved third-disk prediction disagrees with the transform")
    measured = _json_point(check.get("measured_xy_um"), "measured third center")
    distance = math.dist(predicted, measured)
    recorded_distance = _json_number(check.get("error_distance_um"), "third-disk error")
    if not math.isclose(distance, recorded_distance, rel_tol=0, abs_tol=1e-6):
        raise ValueError("the saved third-disk error disagrees with the measurement")
    if distance > VALIDATION_TOLERANCE_UM:
        dx, dy = measured[0] - predicted[0], measured[1] - predicted[1]
        raise ValueError(f"third-disk error {distance:g} µm exceeds {VALIDATION_TOLERANCE_UM:g} µm "
                         f"(measured - predicted: X={dx:+.3f}, Y={dy:+.3f} µm)")


def _verify_measurements(document, transform):
    check = document.get("validation")
    if not isinstance(check, dict) or check.get("passed", True) is not True:
        raise ValueError("missing or failed calibration check")
    disks = _check_measured_fit(document, transform)
    if "method" in check:
        if check != {"method": TWO_EXPOSED_ANCHORS} or not isinstance(transform, SimilarityTransform):
            raise ValueError("invalid two-exposure calibration record")
    else:
        _check_third_disk(check, disks, document["spacing_um"], transform)


def read_validated_calibration(path):
    """Verify the center measurements supporting a completed calibration."""
    try:
        document, transform = _read_calibration(path)
        _verify_measurements(document, transform)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"No calibration at {path}.") from exc
    except ValueError as exc:
        raise ValueError(f"Invalid calibration at {path}: {exc}.") from exc
    return document, transform


def load_validated_plate_calibration(path):
    """Return the spacing and transform after verifying the recorded calibration."""
    document, transform = read_validated_calibration(path)
    return float(document["spacing_um"]), transform


def load_calibration(path):
    """Return the fitted transform, including while third-point validation is pending."""
    _, transform = load_plate_calibration(path)
    return transform
