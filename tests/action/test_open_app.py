"""Tests for the open_app action. No real program is ever launched: _start_file is
always replaced, and the program index is either faked or built from temp files."""
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from desktop_ai_agent.action import handlers
from desktop_ai_agent.action.executor import default_executor
from desktop_ai_agent.action.registry import Risk, ValidationError, build_default_registry


class TempExeCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.launched = []
        patcher = mock.patch.object(handlers, "_start_file", self.launched.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def exe(self, name):
        path = self.dir / name
        path.write_bytes(b"MZ")
        return str(path)

    def index(self, hits):
        patcher = mock.patch.object(handlers, "_search_programs", lambda q, limit: hits)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestMatching(TempExeCase):
    def test_exact_match_launches_the_indexed_path(self):
        steam = self.exe("steam.exe")
        self.index([{"name": "steam", "path": steam}])
        result = handlers.open_app({"name": "steam"})
        self.assertEqual(self.launched, [steam])
        self.assertEqual(result, {"opened": "steam", "path": steam})

    def test_query_is_normalised_like_the_index_does(self):
        code = self.exe("code.exe")
        other = self.exe("vscodium.exe")
        self.index([{"name": "vscodium", "path": other}, {"name": "vs_code", "path": code}])
        handlers.open_app({"name": "VS Code"})
        self.assertEqual(self.launched, [code])

    def test_exact_match_wins_over_longer_names(self):
        steam = self.exe("steam.exe")
        steamvr = self.exe("steamvr.exe")
        self.index([{"name": "steamvr", "path": steamvr}, {"name": "steam", "path": steam}])
        handlers.open_app({"name": "steam"})
        self.assertEqual(self.launched, [steam])

    def test_single_partial_match_launches(self):
        code = self.exe("code.exe")
        self.index([{"name": "vs_code", "path": code}])
        handlers.open_app({"name": "code"})
        self.assertEqual(self.launched, [code])

    def test_several_plausible_matches_ask_instead_of_guessing(self):
        a, b = self.exe("notepad_plus.exe"), self.exe("notepad_x.exe")
        self.index([{"name": "notepad_plus", "path": a}, {"name": "notepad_x", "path": b}])
        with self.assertRaisesRegex(ValueError, "Several programs match.*notepad_plus.*notepad_x.*Ask the user"):
            handlers.open_app({"name": "notepad"})
        self.assertEqual(self.launched, [])

    def test_no_match_is_an_error(self):
        self.index([])
        with self.assertRaisesRegex(LookupError, "No installed program matches 'nothing'"):
            handlers.open_app({"name": "nothing"})
        self.assertEqual(self.launched, [])

    def test_blank_name_is_rejected(self):
        self.index([{"name": "x", "path": self.exe("x.exe")}])
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            handlers.open_app({"name": "   "})
        self.assertEqual(self.launched, [])


class TestOnlyRealExesLaunch(TempExeCase):
    def test_folder_entry_is_refused(self):
        folder = self.dir / "Docker"
        folder.mkdir()
        self.index([{"name": "docker", "path": str(folder)}])
        with self.assertRaisesRegex(ValueError, "none has a launchable .exe"):
            handlers.open_app({"name": "docker"})
        self.assertEqual(self.launched, [])

    def test_stale_index_entry_for_a_missing_file_is_refused(self):
        self.index([{"name": "gone", "path": str(self.dir / "gone.exe")}])
        with self.assertRaisesRegex(ValueError, "launchable"):
            handlers.open_app({"name": "gone"})
        self.assertEqual(self.launched, [])

    def test_non_exe_files_are_refused(self):
        script = self.exe("evil.bat")
        self.index([{"name": "evil", "path": script}])
        with self.assertRaisesRegex(ValueError, "launchable"):
            handlers.open_app({"name": "evil"})
        self.assertEqual(self.launched, [])

    def test_a_launchable_hit_is_used_when_another_hit_is_a_folder(self):
        folder = self.dir / "Docker"
        folder.mkdir()
        desktop = self.exe("docker_desktop.exe")
        self.index([{"name": "docker", "path": str(folder)}, {"name": "docker_desktop", "path": desktop}])
        handlers.open_app({"name": "docker"})
        self.assertEqual(self.launched, [desktop])


class TestPlatform(unittest.TestCase):
    def test_non_windows_gives_a_clear_error(self):
        with mock.patch.object(handlers, "os", SimpleNamespace()):  # no os.startfile
            with self.assertRaisesRegex(RuntimeError, "only supported on Windows"):
                handlers._start_file("C:/x.exe")


class TestRegistryEntry(unittest.TestCase):
    def spec(self):
        return build_default_registry().get("open_app")

    def test_is_medium_risk_and_does_not_end_the_turn(self):
        self.assertEqual(self.spec().risk, Risk.MEDIUM)
        self.assertFalse(self.spec().ends_turn)

    def test_takes_only_a_name_never_a_path(self):
        self.assertEqual(self.spec().validate({"name": "steam"}), {"name": "steam"})
        with self.assertRaisesRegex(ValidationError, "unknown parameter"):
            self.spec().validate({"name": "steam", "path": r"C:\evil.exe"})
        with self.assertRaisesRegex(ValidationError, "unknown parameter"):
            self.spec().validate({"name": "steam", "args": "--flag"})

    def test_name_is_required_and_length_limited(self):
        with self.assertRaisesRegex(ValidationError, "missing required parameter 'name'"):
            self.spec().validate({})
        with self.assertRaisesRegex(ValidationError, "too long"):
            self.spec().validate({"name": "x" * 101})
        with self.assertRaisesRegex(ValidationError, "must be string"):
            self.spec().validate({"name": 5})


class TestThroughTheExecutor(TempExeCase):
    def test_runs_and_is_logged_as_medium_risk(self):
        steam = self.exe("steam.exe")
        self.index([{"name": "steam", "path": steam}])
        ex = default_executor()
        res = ex.run([{"name": "open_app", "arguments": {"name": "steam"}}])
        self.assertTrue(res[0].ok)
        self.assertEqual(res[0].output["opened"], "steam")
        self.assertEqual(self.launched, [steam])
        events = {e["event"]: e for e in ex.audit.entries}
        self.assertEqual(events["execute"]["risk"], "medium")

    def test_runs_without_a_confirmation_dialog(self):
        steam = self.exe("steam.exe")
        self.index([{"name": "steam", "path": steam}])
        res = default_executor(confirm=None).run([{"name": "open_app", "arguments": {"name": "steam"}}])
        self.assertEqual(res[0].status, "executed")

    def test_ambiguity_reaches_the_model_as_an_error_message(self):
        a, b = self.exe("notepad_plus.exe"), self.exe("notepad_x.exe")
        self.index([{"name": "notepad_plus", "path": a}, {"name": "notepad_x", "path": b}])
        res = default_executor().run([{"name": "open_app", "arguments": {"name": "notepad"}}])
        self.assertEqual(res[0].status, "error")
        self.assertIn("Several programs match", res[0].error)
        self.assertEqual(self.launched, [])

    def test_path_argument_is_rejected_by_the_gate_before_any_handler_runs(self):
        res = default_executor().run(
            [{"name": "open_app", "arguments": {"name": "steam", "path": r"C:\evil.exe"}}]
        )
        self.assertEqual(res[0].status, "rejected")
        self.assertEqual(self.launched, [])


class TestWithTheRealFolderIndex(TempExeCase):
    """Uses your real ProgramCatalog search (only the filesystem is faked)."""

    def use_catalog(self, entries):
        from desktop_ai_agent.core.folder_index import tool as tool_mod
        from desktop_ai_agent.core.folder_index.catalog import ProgramCatalog

        previous = tool_mod._catalog
        tool_mod.set_catalog(ProgramCatalog(entries))
        self.addCleanup(tool_mod.set_catalog, previous)

    def entry(self, name, path, display=None):
        return {"name": name, "display": display or name, "path": path,
                "folder": str(self.dir), "root": str(self.dir)}

    def test_finds_and_launches_through_the_real_search(self):
        steam = self.exe("steam.exe")
        self.use_catalog([self.entry("steam", steam, "Steam"), self.entry("git", self.exe("git.exe"))])
        result = handlers.open_app({"name": "Steam"})
        self.assertEqual(self.launched, [steam])
        self.assertEqual(result["opened"], "steam")

    def test_folder_only_entry_is_refused_with_the_real_search(self):
        folder = self.dir / "Docker"
        folder.mkdir()
        self.use_catalog([self.entry("docker", str(folder), "Docker")])
        with self.assertRaisesRegex(ValueError, "launchable"):
            handlers.open_app({"name": "docker"})

    def test_prefix_query_with_two_candidates_asks(self):
        self.use_catalog([
            self.entry("notepad_plus", self.exe("notepad_plus.exe")),
            self.entry("notepad_x", self.exe("notepad_x.exe")),
        ])
        with self.assertRaisesRegex(ValueError, "Several programs match"):
            handlers.open_app({"name": "note"})
        self.assertEqual(self.launched, [])

    def test_unknown_program(self):
        self.use_catalog([self.entry("steam", self.exe("steam.exe"))])
        with self.assertRaises(LookupError):
            handlers.open_app({"name": "zzz_not_installed"})


if __name__ == "__main__":
    unittest.main()