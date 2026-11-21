"""Create and select timestamped DWL carrier runs beside the project scripts."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
import re
import sys
import tempfile

from batch_state import lock_batch_file, read_positions


STAMP_FORMAT = "%y%m%d %H%M%S"


@contextmanager
def machine_lock(project):
    """Exclude overlapping production, calibration, and new-run commands."""
    runs = Path(project).resolve() / "runs"
    runs.mkdir(exist_ok=True)
    lock = runs / ".machine.lock"
    with lock.open("a+b") as stream:
        lock_batch_file(stream, lock)
        yield


def _latest_timestamp(runs):
    if not runs.exists():
        return None
    candidates = []
    for path in runs.iterdir():
        if not re.fullmatch(r"[0-9]{6} [0-9]{6}", path.name) or not path.is_dir():
            continue
        try:
            datetime.strptime(path.name, STAMP_FORMAT)
        except ValueError:
            continue
        candidates.append(path)
    return max(candidates, key=lambda path: path.name, default=None)


def latest_run(project):
    """Select the newest timestamp first, then require its own run files."""
    runs = Path(project).resolve() / "runs"
    folder = _latest_timestamp(runs)
    if folder is None:
        raise FileNotFoundError(f"No carrier run in {runs}. Start one with new-run.bat.")
    for name in ("run-list.txt", "skip-list.txt"):
        path = folder / name
        if not path.is_file():
            raise FileNotFoundError(f"The latest run is missing {path}. Restore the file to resume "
                                    "this carrier, or use new-run.bat for a new carrier.")
        read_positions(path, path.read_bytes())
    return folder


@contextmanager
def active_run(project):
    """Pin the latest carrier for the entire machine operation."""
    with machine_lock(project):
        yield latest_run(project)


def create_run(project):
    project = Path(project).resolve()
    runs = project / "runs"
    with machine_lock(project):
        template = project / "run-list.txt"
        contents = template.read_bytes()
        read_positions(template, contents)
        previous = _latest_timestamp(runs)
        stamp = datetime.now().replace(microsecond=0)
        if previous is not None and stamp.strftime(STAMP_FORMAT) <= previous.name:
            stamp = datetime.strptime(previous.name, STAMP_FORMAT) + timedelta(seconds=1)
        # A single rename publishes both files. An interrupted setup has no timestamped name.
        with tempfile.TemporaryDirectory(dir=runs, prefix=".new-run-") as temp:
            staged = Path(temp) / "carrier"
            staged.mkdir()
            (staged / "run-list.txt").write_bytes(contents)
            (staged / "skip-list.txt").touch(exist_ok=False)
            while True:
                destination = runs / stamp.strftime(STAMP_FORMAT)
                if previous is not None and destination.name <= previous.name:
                    raise ValueError(f"No later run name fits the timestamp format after {previous.name}.")
                if not destination.exists() and not destination.is_symlink():
                    try:
                        return staged.rename(destination)
                    except FileExistsError:
                        pass  # Another program claimed this name before the rename.
                stamp += timedelta(seconds=1)


def add_run_arguments(parser):
    parser.add_argument("--latest-run", action="store_true",
                        help="Pin the newest carrier run (always enabled by the batch launchers)")
    parser.add_argument("--run-list", type=Path, help="Disk list (default: latest run's run-list.txt)")
    parser.add_argument("--skip-list", type=Path, help="Exposure record (default: latest run's skip-list.txt)")
    parser.add_argument("--transform-file", type=Path,
                        help="Verified calibration (default: latest run's plate-calibration.json)")


@contextmanager
def run_session(project, args, *, calibration=False):
    """Resolve state paths once and keep the carrier locked until the command ends."""
    if not args.latest_run and all((args.run_list, args.skip_list, args.transform_file)):
        # Direct Python callers can explicitly resume files from before carrier folders.
        with machine_lock(project):
            yield None
        return

    with active_run(project) as folder:
        for field, name in (("run_list", "run-list.txt"), ("skip_list", "skip-list.txt"),
                            ("transform_file", "plate-calibration.json")):
            supplied = getattr(args, field)
            if calibration and field == "run_list" and supplied is not None:
                continue  # A separate survey list can provide three mounted disks.
            path = folder / name
            if supplied is not None and supplied.resolve() != path.resolve():
                option = "--" + field.replace("_", "-")
                raise ValueError(f"{option} belongs to the selected carrier run: {path}. "
                                 "For a separate set of files, invoke the Python script directly "
                                 "with --run-list, --skip-list and --transform-file.")
            setattr(args, field, path)
        print(f"Using carrier run {folder}", flush=True)
        yield folder


def main(argv=None):
    parser = argparse.ArgumentParser(description="Start a fresh DWL carrier run.")
    parser.parse_args(argv)
    try:
        folder = create_run(Path(__file__).resolve().parent)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Could not start a new run: {exc}", file=sys.stderr)
        return 1
    print(f"New run: {folder}")
    print("Run calibrate.bat for this carrier, then run.bat to expose.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
