"""Print geometry and independent center-check results from a saved DWL calibration."""

import argparse
from pathlib import Path
import sys

from plate_transform import VALIDATION_TOLERANCE_UM, read_validated_calibration


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", type=Path,
                        default=Path(__file__).resolve().with_name("plate-calibration.json"),
                        help="Calibration JSON (default: plate-calibration.json beside this script)")
    path = parser.parse_args(argv).file
    try:
        document, transform = read_validated_calibration(path)
    except (OSError, ValueError) as exc:
        print(f"Cannot summarize calibration {path}: {exc}", file=sys.stderr)
        return 1

    spacing = document["spacing_um"]
    check = document["validation"]
    print(f"Calibration file     {path.resolve()}")
    print(f"Measured disk pitch  {spacing * transform.scale / 1000:.6f} mm")
    print(f"Nominal grid pitch   {spacing / 1000:.6f} mm")
    print(f"Scale                {transform.scale:.9f}")
    print(f"X offset             {transform.tx:+.3f} um")
    print(f"Y offset             {transform.ty:+.3f} um")
    print(f"Rotation             {transform.angle_degrees:+.6f} deg")
    print(f"Validation disk      ({check['row']},{check['col']})")
    print(f"Third-center error   {check['error_distance_um']:.3f} um "
          f"(limit {VALIDATION_TOLERANCE_UM:g} um)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
