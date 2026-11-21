"""Exercise the batch files in a temporary directory, without a machine."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from batch_state import BatchState


class BatchStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.run_file = Path(temporary.name) / "run-list.txt"
        self.skip_file = Path(temporary.name) / "skip-list.txt"
        self.run_file.write_text("# custom order\n (5, 5)\n(1,3) # second\n\n(0,0)\n",
                                 encoding="utf-8-sig")

    def batch(self):
        return BatchState(self.run_file, self.skip_file)

    def test_new_batch_resume_and_reset(self):
        for _ in range(2):
            with self.batch() as batch:
                self.assertEqual(batch.pending, ((5, 5), (1, 3), (0, 0)))
                self.assertEqual(self.skip_file.read_bytes(), b"")
                with batch.starting_exposure((5, 5)):
                    self.assertEqual(self.skip_file.read_bytes(), b"# pending exposure (5,5)\n")
                self.assertEqual(self.skip_file.read_bytes(), b"(5,5)\n")
                with self.assertRaises(RuntimeError):
                    with batch.starting_exposure((5, 5)):
                        self.fail("Replayed the first exposure")
            with self.batch() as batch:
                self.assertEqual(batch.pending, ((1, 3), (0, 0)))
                with batch.starting_exposure((1, 3)):
                    pass
            self.assertEqual(self.skip_file.read_bytes(), b"(5,5)\n(1,3)\n")
            self.skip_file.unlink()

    def test_operator_entries_without_final_newline_and_outside_run_subset(self):
        prefix = b"# manually logged\r\n(2,1)\r\n(5,5)"
        self.skip_file.write_bytes(prefix)
        with self.batch() as batch:
            self.assertEqual(batch.pending, ((1, 3), (0, 0)))
            with batch.starting_exposure((0, 0)):
                pass
        self.assertEqual(self.skip_file.read_bytes(), prefix + b"\n(0,0)\n")

    def test_stop_during_click_preserves_explicit_resolution(self):
        with self.batch() as batch:
            with batch.starting_exposure((5, 5)):
                pass
            with self.assertRaises(KeyboardInterrupt):
                with batch.starting_exposure((1, 3)):
                    raise KeyboardInterrupt
            with self.assertRaises(RuntimeError):
                with batch.starting_exposure((0, 0)):
                    self.fail("Continued after an unresolved start")
        interrupted = self.skip_file.read_bytes()
        self.assertEqual(interrupted, b"(5,5)\n# pending exposure (1,3)\n")
        with self.assertRaisesRegex(RuntimeError, r"uncertain for \(1,3\)"):
            with self.batch():
                self.fail("Should stop for the operator to check the exposure")
        for resolved, pending in [(b"(1,3)\n", ((0, 0),)), (b"", ((1, 3), (0, 0)))]:
            self.skip_file.write_bytes(interrupted.replace(b"# pending exposure (1,3)\n", resolved))
            with self.batch() as batch:
                self.assertEqual(batch.pending, pending)

    def test_state_write_failure_prevents_click_or_resume(self):
        with self.batch() as batch, patch("batch_state.os.fsync", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                with batch.starting_exposure((5, 5)):
                    self.fail("Requested a click before syncing its marker")
        with self.assertRaisesRegex(RuntimeError, "Exposure start is uncertain"):
            with self.batch():
                self.fail("Resumed with a pending marker")

    def test_interruption_during_cleanup_leaves_parseable_started_record(self):
        with self.batch() as batch, patch("batch_state.os.fsync",
                                          side_effect=[None, OSError("interrupted")]):
            with self.assertRaisesRegex(OSError, "interrupted"):
                with batch.starting_exposure((5, 5)):
                    pass
        with self.batch() as batch:
            self.assertEqual(batch.pending, ((1, 3), (0, 0)))
            with batch.starting_exposure((1, 3)):
                pass
        with self.batch() as batch:
            self.assertEqual(batch.pending, ((0, 0),))

    def test_external_edit_before_confirm_prevents_click(self):
        with self.batch() as batch:
            with self.skip_file.open("ab") as editor:
                editor.write(b"(1,3)\n")
            with self.assertRaisesRegex(RuntimeError, "edited during"):
                with batch.starting_exposure((5, 5)):
                    self.fail("Continued with a stale batch")
        self.assertEqual(self.skip_file.read_bytes(), b"(1,3)\n")

    def test_external_edit_during_confirm_preserved_and_restart_blocked(self):
        with self.batch() as batch, self.assertRaisesRegex(RuntimeError, "edited while starting"):
            with batch.starting_exposure((5, 5)):
                # Overwrite the complete marker line, simulating an editor.
                with self.skip_file.open("r+b") as editor:
                    editor.write(b"# replacement content from the operator\n")
        self.assertIn(b"# replacement content from the operator\n", self.skip_file.read_bytes())
        with self.assertRaisesRegex(RuntimeError, "Exposure start is uncertain"):
            with self.batch():
                self.fail("Resumed despite concurrent edits")

    def test_overlapping_batch_is_blocked_until_first_closes(self):
        with self.batch():
            with self.assertRaisesRegex(RuntimeError, "another batch"):
                with self.batch():
                    self.fail("Two simultaneous batch writers")
        with self.batch() as reopened:
            self.assertEqual(len(reopened.pending), 3)

    def test_invalid_lists_do_not_start_a_batch(self):
        self.skip_file.write_text("(0,0)\n", encoding="utf-8")
        for path in [self.run_file, self.skip_file]:
            original = path.read_bytes()
            for invalid in [b"(6,0)\n", b"(2,3),(1,4)\n", b"(1,4)\n(1, 4)\n"]:
                with self.subTest(path=path, invalid=invalid):
                    path.write_bytes(invalid)
                    with self.assertRaisesRegex(ValueError, path.name):
                        with self.batch():
                            self.fail("Accepted invalid list")
                    self.assertEqual(path.read_bytes(), invalid)
            path.write_bytes(original)

    def test_missing_run_list_does_not_create_a_skip_file(self):
        self.run_file.unlink()
        with self.assertRaises(FileNotFoundError):
            with self.batch():
                self.fail("Ran without a run list")
        self.assertFalse(self.skip_file.exists())
