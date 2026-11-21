"""Track exposure starts for one 6×6 plate in two operator-editable text files."""

from contextlib import contextmanager
import os
from pathlib import Path
import re


_POSITION = re.compile(r"\(\s*([0-5])\s*,\s*([0-5])\s*\)")
_PENDING = "# pending exposure "


def _read_positions(path, data, *, check_pending=False):
    positions = []
    for number, line in enumerate(data.decode("utf-8-sig").splitlines(), 1):
        line = line.strip()
        if check_pending and line.startswith(_PENDING.rstrip()):
            position = line[len(_PENDING):].strip()
            raise RuntimeError(
                f"{path}:{number}: Exposure start is uncertain for {position}. "
                "Check the machine's job history. If exposure started, replace the "
                "pending line with the position. If it definitely did not start, "
                "remove that line before resuming."
            )
        line = line.partition("#")[0].strip()
        if not line:
            continue
        match = _POSITION.fullmatch(line)
        if not match:
            raise ValueError(f"{path}:{number}: expected (row,col), each from 0 to 5; got {line!r}")
        position = tuple(map(int, match.groups()))
        if position in positions:
            raise ValueError(f"{path}:{number}: duplicate position {position}")
        positions.append(position)
    return positions


def _lock_skip_list(stream, path):
    try:
        if os.name == "nt":
            import msvcrt
            # Windows locks actual bytes. Lock beyond this short text file so
            # the operator can still open it for reading while the batch runs.
            stream.seek(1 << 30)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise RuntimeError(f"Could not lock {path}. Check whether another batch is running.") from exc
    stream.seek(0)


class BatchState:
    """Hold the skip-list lock from startup through the end of the machine run."""

    def __init__(self, run_path, skip_path):
        self.run_path = Path(run_path)
        self.skip_path = Path(skip_path)
        self._stream = None

    def __enter__(self):
        run = _read_positions(self.run_path, self.run_path.read_bytes())
        if self.skip_path.exists() and self.run_path.samefile(self.skip_path):
            raise ValueError("The run list and skip list must be different files.")
        fd = os.open(self.skip_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o666)
        stream = os.fdopen(fd, "r+b")
        try:
            _lock_skip_list(stream, self.skip_path)
            self._expected = stream.read()
            skipped = _read_positions(self.skip_path, self._expected, check_pending=True)
            self.pending = tuple(position for position in run if position not in skipped)
            self._remaining = set(self.pending)
            self._in_flight = False
            self._stream = stream
        except BaseException:
            stream.close()
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._stream.close()  # the OS also releases the lock if the process exits unexpectedly
        self._stream = None

    def _current_bytes(self):
        if not os.path.samestat(os.fstat(self._stream.fileno()), self.skip_path.stat()):
            raise RuntimeError(f"{self.skip_path} was replaced during the batch.")
        self._stream.seek(0)
        return self._stream.read()

    def _sync(self):
        self._stream.flush()
        os.fsync(self._stream.fileno())

    @contextmanager
    def starting_exposure(self, position):
        """Bracket the single click that starts writing. An interrupted click stays unresolved."""
        stream = self._stream
        if stream is None or self._in_flight or position not in self._remaining:
            raise RuntimeError(f"Cannot start exposure for {position} in the current batch.")
        self._in_flight = True
        if self._current_bytes() != self._expected:
            raise RuntimeError(f"{self.skip_path} was edited during the batch. "
                               "Check the exposure history before resuming.")
        row, col = position
        record = f"({row},{col})\n".encode("ascii")
        if self._expected and not self._expected.endswith(b"\n"):
            stream.write(b"\n")
            self._expected += b"\n"
        marker_offset = stream.tell()
        marker = _PENDING.encode("ascii") + record
        stream.write(marker)
        self._sync()

        try:
            yield
        finally:
            current = self._current_bytes()
            if current != self._expected + marker:
                # Keep concurrent edits. Leave an unresolved start even if the
                # editor removed our marker and the click itself then failed.
                if marker.rstrip() not in current.splitlines():
                    if current and not current.endswith(b"\n"):
                        stream.write(b"\n")
                    stream.write(marker)
                    self._sync()
                raise RuntimeError(f"{self.skip_path} was edited while starting exposure for {position}. "
                                   "Check the exposure history before resuming.")
        stream.seek(marker_offset)
        # The padded form is itself a valid skip list if interrupted before trimming.
        stream.write(record.ljust(len(marker), b" "))
        self._sync()
        stream.truncate(marker_offset + len(record))
        self._sync()
        self._expected += record
        self._remaining.remove(position)
        self._in_flight = False
