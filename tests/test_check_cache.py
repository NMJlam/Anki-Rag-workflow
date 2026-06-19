from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from reason.check import (
    NoteReport,
    _is_trivial_imprecision,
    _should_suppress_issue,
    check_all_notes,
)
from sync.index import NoteEntry, save_index
from sync.vault import _sha256


class CheckCacheTests(unittest.TestCase):
    def test_treats_close_paraphrase_as_trivial_imprecision(self) -> None:
        self.assertTrue(_is_trivial_imprecision(
            "Space sharing is dividing a shared resource (in space) by those who wish to use it",
            "Space sharing is where a resource is divided (in space) among those who wish to use it.",
        ))

    def test_suppresses_close_paraphrase_even_when_marked_wrong(self) -> None:
        self.assertTrue(_should_suppress_issue(
            "wrong",
            "Scheduled - switched from ready to running",
            "Scheduled means being moved from ready to running.",
        ))

    def test_suppresses_textbook_meta_imprecision(self) -> None:
        self.assertTrue(_should_suppress_issue(
            "imprecise",
            (
                "A process transitions between the states running, ready and "
                "blocked, but also starts from initial and ends in final states."
            ),
            (
                "The textbook describes a process transitioning between running, "
                "ready, and blocked states, and also mentions other states like "
                "UNUSED, EMBRYO, SLEEPING, and ZOMBIE in the xv6 example, but "
                "does not explicitly define 'initial' and 'final' states as "
                "standard terms in the basic state transition diagram."
            ),
        ))

    def test_suppresses_scheduler_extra_detail_imprecision(self) -> None:
        self.assertTrue(_should_suppress_issue(
            "imprecise",
            (
                "The transitions are by the discretion of the OS scheduler "
                "deciding to run the process."
            ),
            (
                "Transitions between the ready and running states are at the "
                "discretion of the OS scheduler. However, transitions to/from "
                "the blocked state are triggered by events like I/O initiation "
                "or completion."
            ),
        ))

    def test_suppresses_context_switch_extra_detail_imprecision(self) -> None:
        self.assertTrue(_should_suppress_issue(
            "imprecise",
            (
                "When a process is resumed, the OS restores the register "
                "context out of the PCB and loads it into the CPU to resume "
                "the process."
            ),
            (
                "When a process is resumed, the OS restores the register "
                "context from the process's process structure (PCB) and loads "
                "it into the CPU, but this restoration is typically part of "
                "the switch() routine that also switches to the process's "
                "kernel stack, and the final step of resuming execution is "
                "performed by the return-from-trap instruction."
            ),
        ))

    def test_suppresses_process_list_tracking_extra_detail(self) -> None:
        self.assertTrue(_should_suppress_issue(
            "imprecise",
            (
                "The process list tracks all the processes in a system, "
                "including those that are ready, blocked, or running."
            ),
            (
                "The process list tracks all processes in the system, but the "
                "passage specifies that the OS keeps a process list for all "
                "processes that are ready, and separately tracks the currently "
                "running process and blocked processes."
            ),
        ))

    def test_suppresses_register_restore_subcase_extra_detail(self) -> None:
        self.assertTrue(_should_suppress_issue(
            "imprecise",
            (
                "When a process is resumed, the OS restores the register "
                "context out of the PCB and loads it into the CPU to resume "
                "the process."
            ),
            (
                "When a process is resumed, the OS restores the register "
                "context from the PCB and loads it into the CPU. However, the "
                "specific registers restored depend on the context: for a "
                "timer interrupt, the hardware initially saves user registers "
                "to the kernel stack, and the OS saves kernel registers to "
                "the PCB during a switch."
            ),
        ))

    def test_suppresses_wait_exit_status_api_detail(self) -> None:
        self.assertTrue(_should_suppress_issue(
            "imprecise",
            (
                "This allows other processes, usually the parent, to examine "
                "the return code and determine if the process has executed "
                "successfully."
            ),
            (
                "The wait() system call allows a parent process to wait for a "
                "child process to finish. The return value of wait() is the "
                "PID of the child that terminated. To examine the return code "
                "(exit status) of the child, one must pass a pointer to an "
                "integer to wait() and then use macros like WIFEXITED() and "
                "WEXITSTATUS() to interpret it."
            ),
        ))

    def test_skips_unchanged_committed_note(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            vault.mkdir()
            state = root / "state.sqlite"
            note = vault / "note.md"
            note.write_text("Already committed.\n")

            index = __import__("sync.index", fromlist=["SyncIndex"]).SyncIndex()
            index.upsert_note(
                "note.md",
                NoteEntry(
                    committed_file_hash=_sha256(note.read_text()),
                    last_processed="now",
                    deck="",
                ),
            )
            save_index(index, state)

            with patch("reason.check.check_note") as check_note:
                reports = check_all_notes(vault, state_path=state)

            self.assertEqual(reports, [])
            check_note.assert_not_called()

    def test_marks_clean_changed_note_as_checked(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            vault.mkdir()
            state = root / "state.sqlite"
            note = vault / "note.md"
            note.write_text("Changed content.\n")
            checked = []

            def fake_check_note(rel_path: str, content: str, **_: object) -> NoteReport:
                checked.append((rel_path, content))
                return NoteReport(rel_path)

            with patch("reason.check.check_note", side_effect=fake_check_note):
                first = check_all_notes(vault, state_path=state)
                second = check_all_notes(vault, state_path=state)

            self.assertEqual(len(first), 1)
            self.assertEqual(second, [])
            self.assertEqual(checked, [("note.md", "Changed content.\n")])


if __name__ == "__main__":
    unittest.main()
