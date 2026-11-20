"""Arithmetic-only calibration tests; no browser or machine API is used."""

import copy
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import plate_transform
from plate_transform import (
    RigidTransform, SimilarityTransform, fit_rigid_transform, fit_similarity_transform,
    load_calibration, plate_position, save_calibration,
)


class PlateTransformTests(unittest.TestCase):
    def assertPointAlmostEqual(self, actual, expected):
        for got, wanted in zip(actual, expected):
            self.assertAlmostEqual(got, wanted, places=8)

    def test_grid_convention_and_fractional_centers(self):
        self.assertEqual(plate_position(0, 0), (-71222.5, 71222.5))
        self.assertEqual(plate_position(5, 5), (71222.5, -71222.5))
        self.assertEqual(plate_position(0, 5), (-71222.5, -71222.5))
        self.assertEqual(plate_position(2, 3, spacing=20), (-10.0, -10.0))

    def test_identity(self):
        a, b = plate_position(0, 0), plate_position(5, 5)
        fit = fit_rigid_transform(a, b, a, b)
        self.assertAlmostEqual(fit.angle_degrees, 0)
        self.assertPointAlmostEqual(fit.apply(plate_position(0, 5)), plate_position(0, 5))

    def test_translation(self):
        fit = fit_rigid_transform((1, 2), (5, 8), (101, -18), (105, -12))
        self.assertAlmostEqual(fit.tx, 100)
        self.assertAlmostEqual(fit.ty, -20)
        self.assertPointAlmostEqual(fit.apply((10, 30)), (110, 10))

    def test_rotations_and_translation_predict_all_disks(self):
        a, b = plate_position(0, 0), plate_position(5, 5)
        for angle in (-179, -90, -2.5, 0.1, 30, 90, 179):
            with self.subTest(angle=angle):
                theta = math.radians(angle)
                expected = RigidTransform(math.cos(theta), math.sin(theta), 123.4, -56.7)
                fit = fit_rigid_transform(a, b, expected.apply(a), expected.apply(b))
                self.assertAlmostEqual(fit.angle_degrees, angle)
                for row in range(6):
                    for col in range(6):
                        point = plate_position(row, col)
                        self.assertPointAlmostEqual(fit.apply(point), expected.apply(point))

    def test_unequal_lengths_do_not_introduce_scale(self):
        fit = fit_rigid_transform((0, 0), (10, 0), (100, 200), (100, 212))
        self.assertPointAlmostEqual(fit.apply((0, 0)), (100, 201))
        self.assertPointAlmostEqual(fit.apply((10, 0)), (100, 211))
        self.assertAlmostEqual(math.dist(fit.apply((0, 0)), fit.apply((10, 0))), 10)
        self.assertAlmostEqual(fit.angle_degrees, 90)

    def test_pair_order_can_be_reversed_consistently(self):
        first = fit_rigid_transform((0, 1), (5, 7), (4, 2), (9, 8))
        reverse = fit_rigid_transform((5, 7), (0, 1), (9, 8), (4, 2))
        self.assertEqual(first, reverse)

    def test_invalid_indices_and_spacing(self):
        for bad in (-1, 6, 0.5, "0", True, None):
            with self.subTest(index=bad):
                with self.assertRaises(ValueError):
                    plate_position(bad, 0)
                with self.assertRaises(ValueError):
                    plate_position(0, bad)
        for bad in (0, -1, math.inf, math.nan, None, "bad", 1e308):
            with self.subTest(spacing=bad), self.assertRaises(ValueError):
                plate_position(0, 0, bad)

    def test_degenerate_pairs_are_rejected(self):
        with self.assertRaises(ValueError):
            fit_rigid_transform((1, 1), (1, 1), (1, 2), (3, 4))
        with self.assertRaises(ValueError):
            fit_rigid_transform((1, 2), (3, 4), (1, 1), (1, 1))
        with self.assertRaises(ValueError):
            fit_rigid_transform((-1e308, 0), (1e308, 0), (0, 0), (1, 1))

    def test_bad_coordinates_are_rejected_in_every_input_position(self):
        good = [(0, 0), (1, 1), (2, 2), (3, 3)]
        for bad in ((math.inf, 0), (0, math.nan), (0,), (0, 1, 2), "12", None):
            for index in range(4):
                arguments = good.copy()
                arguments[index] = bad
                with self.subTest(bad=bad, index=index), self.assertRaises(ValueError):
                    fit_rigid_transform(*arguments)

    def test_transform_rejects_nonrigid_and_nonfinite_values(self):
        for parameters in ((2, 0, 0, 0), (0, 0, 0, 0), (1, 0, math.inf, 0)):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                RigidTransform(*parameters)
        with self.assertRaises(ValueError):
            RigidTransform(1, 0, 0, 0).apply((math.nan, 0))


class CalibrationFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / "plate-calibration.json"
        self.transform = RigidTransform(math.cos(0.1), math.sin(0.1), 123.4, -567.8)
        self.points = [{"row": 0, "col": 0, "measured_um": [-70000, 71000]},
                       {"row": 5, "col": 5, "measured_um": [72000, -71000]}]

    def save(self, **options):
        arguments = dict(spacing=28489.0, calibration_points=self.points)
        arguments.update(options)
        save_calibration(self.path, self.transform, **arguments)

    def test_roundtrip_pending_and_completed_validation(self):
        for validation in (None, {"status": "pending"},
                           {"error_x_um": 1.2, "error_y_um": -3.4}):
            with self.subTest(validation=validation):
                self.save(validation=validation)
                self.assertEqual(load_calibration(self.path), self.transform)
                document = json.loads(self.path.read_text(encoding="utf-8"))
                self.assertEqual(document["calibration_points"], self.points)
                self.assertEqual(document["validation"], validation)
                self.assertEqual(document["rotation_matrix"], [
                    [self.transform.cos_theta, -self.transform.sin_theta],
                    [self.transform.sin_theta, self.transform.cos_theta]])

    def test_unknown_finite_metadata_is_ignored(self):
        self.save()
        document = json.loads(self.path.read_text(encoding="utf-8"))
        document["future_metadata"] = {"distance": 123.0, "note": "extra metadata"}
        self.path.write_text(json.dumps(document), encoding="utf-8")
        self.assertEqual(load_calibration(self.path), self.transform)

    def test_malformed_fields_are_rejected(self):
        self.save()
        original = json.loads(self.path.read_text(encoding="utf-8"))
        invalid = {
            "version": [0, 2, True, "1", None],
            "model": ["affine", None],
            "units": ["mm", None],
            "spacing_um": [0, -1, math.nan, math.inf, "28489", True, None],
            "rotation_matrix": [None, [], [1, 2], [[1, 0, 0], [0, 1]],
                                [[2, 0], [0, 2]], [[1, 0], [0, -1]],
                                [[1, 0.1], [0, 1]], [[math.inf, 0], [0, 1]],
                                [["1", 0], [0, 1]], [[True, 0], [0, 1]]],
            "translation_um": [None, [], [1], [1, 2, 3], [0, math.nan], [True, 0]],
            "calibration_points": [None, [], [{}], [{}, {}, {}], [1, {}]],
            "validation": [[], "passed"],
        }
        for key, values in invalid.items():
            for value in values:
                document = copy.deepcopy(original)
                document[key] = value
                self.path.write_text(json.dumps(document), encoding="utf-8")
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    load_calibration(self.path)
        for key in ("version", "model", "units", "spacing_um", "rotation_matrix",
                    "translation_um", "calibration_points"):
            document = copy.deepcopy(original)
            del document[key]
            self.path.write_text(json.dumps(document), encoding="utf-8")
            with self.subTest(missing=key), self.assertRaises(ValueError):
                load_calibration(self.path)

    def test_corrupt_and_duplicate_json_fields_are_rejected(self):
        self.save()
        serialized = self.path.read_text(encoding="utf-8")
        for text in ("{bad", "[]", serialized.replace('"version": 1', '"version": 1, "version": 1')):
            self.path.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(ValueError):
                load_calibration(self.path)

    def test_nonfinite_extra_metadata_is_rejected(self):
        self.save()
        document = json.loads(self.path.read_text(encoding="utf-8"))
        document["extra"] = {"values": [math.inf]}
        self.path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_calibration(self.path)

    def test_atomic_replacement_preserves_old_file_until_complete(self):
        self.path.write_text("old complete file", encoding="utf-8")
        replace = plate_transform.os.replace

        def observe_replace(source, target):
            self.assertEqual(self.path.read_text(encoding="utf-8"), "old complete file")
            self.assertEqual(load_calibration(source), self.transform)
            self.assertEqual(Path(source).parent, self.path.parent)
            replace(source, target)

        with mock.patch.object(plate_transform.os, "replace", side_effect=observe_replace) as patched:
            self.save()
        patched.assert_called_once()
        self.assertEqual(load_calibration(self.path), self.transform)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_failed_replace_keeps_old_file_and_removes_temporary(self):
        self.path.write_text("old complete file", encoding="utf-8")
        with mock.patch.object(plate_transform.os, "replace", side_effect=OSError("locked")):
            with self.assertRaises(OSError):
                self.save()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "old complete file")
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_invalid_save_does_not_touch_existing_file(self):
        self.path.write_text("old complete file", encoding="utf-8")
        for options in ({"spacing": math.inf}, {"spacing": 0},
                        {"calibration_points": [{}]},
                        {"validation": {"error": math.nan}},
                        {"validation": {"unsupported": object()}}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.save(**options)
            self.assertEqual(self.path.read_text(encoding="utf-8"), "old complete file")
        self.assertEqual(list(self.directory.iterdir()), [self.path])


class SimilarityTransformTests(unittest.TestCase):
    def assertPointAlmostEqual(self, actual, expected):
        for got, wanted in zip(actual, expected):
            self.assertAlmostEqual(got, wanted, places=8)

    def test_scaled_rotated_fit_predicts_all_disk_centers(self):
        pa, pb = plate_position(0, 0), plate_position(5, 5)
        for scale in (0.5, 1.0, 1.015, 2.0):
            for degrees in (-160, -2, 0, 0.3, 90):
                with self.subTest(scale=scale, degrees=degrees):
                    radians = math.radians(degrees)
                    expected = SimilarityTransform(math.cos(radians), math.sin(radians),
                                                   1234.5, -987.6, scale)
                    actual = fit_similarity_transform(pa, pb, expected.apply(pa), expected.apply(pb))
                    self.assertAlmostEqual(actual.scale, scale)
                    self.assertAlmostEqual(actual.angle_degrees, degrees)
                    for row in range(6):
                        for col in range(6):
                            point = plate_position(row, col)
                            self.assertPointAlmostEqual(actual.apply(point), expected.apply(point))

    def test_measured_centers_and_independent_third_prediction(self):
        pa, pb = plate_position(0, 0), plate_position(5, 5)
        qa, qb = (-76392.7, 70725.3), (68792.0, -73434.7)
        fit = fit_similarity_transform(pa, pb, qa, qb)
        self.assertPointAlmostEqual(fit.apply(pa), qa)
        self.assertPointAlmostEqual(fit.apply(pb), qb)
        self.assertAlmostEqual(fit.scale, 1.0156429304524346, places=14)
        self.assertAlmostEqual(fit.angle_degrees, 0.20290933208842174, places=12)
        self.assertPointAlmostEqual((fit.tx, fit.ty), (-3800.35, -1354.7))
        self.assertPointAlmostEqual(fit.apply(plate_position(0, 5)), (-75880.35, -73947.05))

    def test_noncentered_points_and_reversed_order(self):
        expected = SimilarityTransform(0, -1, 123, -45, 1.5)
        pa, pb = (4, 7), (11, 16)
        fit = fit_similarity_transform(pa, pb, expected.apply(pa), expected.apply(pb))
        reverse = fit_similarity_transform(pb, pa, expected.apply(pb), expected.apply(pa))
        self.assertPointAlmostEqual(fit.apply((8, 10)), expected.apply((8, 10)))
        self.assertPointAlmostEqual(reverse.apply((8, 10)), expected.apply((8, 10)))

    def test_complete_matrix_matches_apply(self):
        for transform in (RigidTransform(0, 1, 10, 20),
                          SimilarityTransform(0, 1, 10, 20, 1.5)):
            vector = [3, -7, 1]
            result = [sum(a * b for a, b in zip(row, vector)) for row in transform.matrix]
            self.assertEqual(result[2], 1.0)
            self.assertPointAlmostEqual(result[:2], transform.apply(vector[:2]))

    def test_invalid_and_degenerate_similarity_inputs(self):
        for scale in (0, -1, math.nan, math.inf, None):
            with self.subTest(scale=scale), self.assertRaises(ValueError):
                SimilarityTransform(1, 0, 0, 0, scale)
        for arguments in (((0, 0), (0, 0), (1, 2), (3, 4)),
                          ((1, 2), (3, 4), (0, 0), (0, 0)),
                          ((math.inf, 0), (1, 0), (0, 0), (1, 1)),
                          ((0, 0), (1e-300, 0), (0, 0), (1e300, 0)),
                          ((0, 0), (1e300, 0), (0, 0), (1e-300, 0))):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                fit_similarity_transform(*arguments)
        with self.assertRaises(ValueError):
            SimilarityTransform(1, 0, 0, 0, 2).apply((1e308, 0))


class SimilarityFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "scaled.json"
        self.transform = SimilarityTransform(math.cos(0.03), math.sin(0.03),
                                             -3800.35, -1354.7, 1.0156429304524346)
        save_calibration(self.path, self.transform, spacing=28489.0,
                         calibration_points=[{"row": 0, "col": 0}, {"row": 5, "col": 5}])
        self.document = json.loads(self.path.read_text(encoding="utf-8"))

    def test_v2_roundtrip_preserves_full_transform(self):
        self.assertEqual(self.document["version"], 2)
        self.assertEqual(self.document["model"], "similarity_2d")
        self.assertEqual(self.document["scale"], self.transform.scale)
        self.assertEqual(self.document["transform_matrix"], self.transform.matrix)
        loaded = load_calibration(self.path)
        self.assertIsInstance(loaded, SimilarityTransform)
        self.assertEqual(loaded, self.transform)
        self.assertEqual(loaded.apply((1234, -5678)), self.transform.apply((1234, -5678)))

    def test_invalid_v2_scale_matrix_and_model_are_rejected(self):
        invalid = {
            "scale": [None, 0, -1, True, "1", math.nan, math.inf, 1.1],
            "version": [1, 3, True, None],
            "model": ["rigid_2d", "affine_2d", None],
            "transform_matrix": [None, [], [[1, 0], [0, 1]],
                                 [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                                 [[1, 0, 0], [0, 1, 0], [0, 0, 0]],
                                 [[1, 0, 0], [0, 1, 0], [0, 0, math.inf]]],
        }
        for key, values in invalid.items():
            for value in values:
                document = copy.deepcopy(self.document)
                document[key] = value
                self.path.write_text(json.dumps(document), encoding="utf-8")
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    load_calibration(self.path)
        for key in ("scale", "transform_matrix"):
            document = copy.deepcopy(self.document)
            del document[key]
            self.path.write_text(json.dumps(document), encoding="utf-8")
            with self.subTest(missing=key), self.assertRaises(ValueError):
                load_calibration(self.path)

    def test_full_matrix_cannot_disagree_with_components(self):
        for row, col in ((0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2), (2, 0), (2, 1), (2, 2)):
            document = copy.deepcopy(self.document)
            document["transform_matrix"][row][col] += 0.001
            self.path.write_text(json.dumps(document), encoding="utf-8")
            with self.subTest(row=row, col=col), self.assertRaises(ValueError):
                load_calibration(self.path)

    def test_legacy_without_matrix_still_loads_but_cannot_ignore_scale(self):
        document = copy.deepcopy(self.document)
        document.update(version=1, model="rigid_2d")
        del document["scale"]
        del document["transform_matrix"]
        self.path.write_text(json.dumps(document), encoding="utf-8")
        loaded = load_calibration(self.path)
        self.assertIs(type(loaded), RigidTransform)
        self.assertEqual(loaded.scale, 1.0)
        for scale in (1.01, 0, -1, True, None, "1"):
            document["scale"] = scale
            self.path.write_text(json.dumps(document), encoding="utf-8")
            with self.subTest(scale=scale), self.assertRaises(ValueError):
                load_calibration(self.path)
        document["scale"] = 1.0
        document["transform_matrix"] = self.transform.matrix
        self.path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_calibration(self.path)


if __name__ == "__main__":
    unittest.main()
