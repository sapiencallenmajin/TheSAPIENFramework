"""Tests for --scenario-ids filtering in setup_engine."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.commands.scan_orchestration import setup_engine
from sapien_score.commands.scan_output import (
    _atomic_write_json,
    compute_results_checksum,
)


def _make_scenario(id: str, domain: str = "medical"):
    """Return a minimal mock scenario with .id and .domain."""
    s = MagicMock()
    s.id = id
    s.domain = domain
    return s


# Shared defaults for setup_engine kwargs that aren't under test.
_DEFAULTS = dict(
    model="test/model",
    judge_model=None,
    domain=None,
    domains=None,
    run_all=True,
    output=None,
    verbose=False,
    persona=None,
    memory=None,
    profile=None,
    avg_tokens=800,
    resume=None,
    retry_delay=2.0,
    debug=False,
    collection="sapien",
    authorship=None,
    audience=None,
    scenarios_dir_override=None,
    tier_override="auto",
    no_counter_refusals=False,
    no_trace=True,
    replay=None,
    allow_trace_during_replay=False,
    layer2_threshold=0.0,
    console=MagicMock(),
    override_rules=[],
)


_CORPUS = [
    _make_scenario("alpha"),
    _make_scenario("bravo"),
    _make_scenario("charlie"),
]


class TestScenarioIdsFilter:

    @patch("sapien_score.model_profiles.get_model_profile")
    @patch("sapien_score.engine.adapter.get_adapter")
    @patch("sapien_score.scenarios.loader.load_all_scenarios", return_value=list(_CORPUS))
    def test_scenario_ids_filters_to_matching_ids(self, mock_load, mock_adapter, mock_profile):
        engine = setup_engine(**_DEFAULTS, scenario_ids="alpha,charlie")
        result_ids = {s.id for s in engine.scenarios}
        assert result_ids == {"alpha", "charlie"}

    @patch("sapien_score.scenarios.loader.load_all_scenarios", return_value=list(_CORPUS))
    def test_scenario_ids_unknown_id_fails(self, mock_load):
        console = MagicMock()
        with pytest.raises(SystemExit) as exc_info:
            setup_engine(**{**_DEFAULTS, "console": console}, scenario_ids="alpha,bogus_id")
        assert exc_info.value.code == 1
        printed = console.print.call_args_list
        assert any("bogus_id" in str(call) for call in printed)

    @patch("sapien_score.model_profiles.get_model_profile")
    @patch("sapien_score.engine.adapter.get_adapter")
    @patch("sapien_score.scenarios.loader.load_all_scenarios", return_value=list(_CORPUS))
    def test_scenario_ids_overrides_domain_filter_with_warning(self, mock_load, mock_adapter, mock_profile):
        console = MagicMock()
        engine = setup_engine(
            **{**_DEFAULTS, "console": console, "domain": "medical"},
            scenario_ids="bravo",
        )
        result_ids = {s.id for s in engine.scenarios}
        assert result_ids == {"bravo"}
        printed = " ".join(str(c) for c in console.print.call_args_list)
        assert "ignoring" in printed.lower()


# ---------------------------------------------------------------------------
# P0-10: atomic-write helper
# ---------------------------------------------------------------------------

class TestAtomicWriteJson:
    def test_atomic_write_creates_and_backs_up(self, tmp_path):
        target = tmp_path / "out.json"
        _atomic_write_json(str(target), {"v": 1})
        assert target.exists()
        assert json.loads(target.read_text()) == {"v": 1}

        _atomic_write_json(str(target), {"v": 2})
        backup = tmp_path / "out.backup.json"
        assert backup.exists(), "previous contents should be backed up"
        assert json.loads(backup.read_text()) == {"v": 1}
        assert json.loads(target.read_text()) == {"v": 2}

    def test_atomic_write_no_tmp_leftover_on_failure(self, tmp_path):
        """An os.replace failure must not leave a dangling .tmp file."""
        target = tmp_path / "out.json"
        with patch(
            "sapien_score.commands.scan_output.os.replace",
            side_effect=OSError("simulated rename failure"),
        ):
            with pytest.raises(OSError, match="simulated rename"):
                _atomic_write_json(str(target), {"v": 1})
        # Even with the rename failure, no .tmp must be left behind.
        residual = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert residual == []


# ---------------------------------------------------------------------------
# P0-11: resume checksum validation
# ---------------------------------------------------------------------------

class TestResumeChecksum:
    def _write_resume(self, path, entries, include_checksum=True, bad_checksum=False):
        payload = {"model": "test/model", "results": entries}
        if include_checksum:
            payload["_checksum"] = (
                "0" * 64 if bad_checksum else compute_results_checksum(entries)
            )
        _atomic_write_json(str(path), payload)

    @patch("sapien_score.scenarios.loader.load_all_scenarios", return_value=list(_CORPUS))
    def test_resume_missing_checksum_rejected(self, mock_load, tmp_path):
        resume = tmp_path / "prior.json"
        self._write_resume(resume, [{"scenario_id": "alpha", "health_score": 90, "verdict": "MAINTAINED"}], include_checksum=False)
        console = MagicMock()
        with pytest.raises(SystemExit) as exc_info:
            setup_engine(**{**_DEFAULTS, "console": console, "resume": str(resume)})
        assert exc_info.value.code == 1
        printed = " ".join(str(c) for c in console.print.call_args_list)
        assert "_checksum" in printed or "integrity" in printed.lower()

    @patch("sapien_score.scenarios.loader.load_all_scenarios", return_value=list(_CORPUS))
    def test_resume_bad_checksum_rejected(self, mock_load, tmp_path):
        resume = tmp_path / "prior.json"
        self._write_resume(
            resume,
            [{"scenario_id": "alpha", "health_score": 90, "verdict": "MAINTAINED"}],
            bad_checksum=True,
        )
        console = MagicMock()
        with pytest.raises(SystemExit) as exc_info:
            setup_engine(**{**_DEFAULTS, "console": console, "resume": str(resume)})
        assert exc_info.value.code == 1
        printed = " ".join(str(c) for c in console.print.call_args_list)
        assert "checksum" in printed.lower()

    @patch("sapien_score.model_profiles.get_model_profile")
    @patch("sapien_score.engine.adapter.get_adapter")
    @patch("sapien_score.scenarios.loader.load_all_scenarios", return_value=list(_CORPUS))
    def test_force_resume_bypasses_validation(self, mock_load, mock_adapter, mock_profile, tmp_path):
        resume = tmp_path / "prior.json"
        self._write_resume(
            resume,
            [{"scenario_id": "alpha", "health_score": 90, "verdict": "MAINTAINED"}],
            bad_checksum=True,
        )
        console = MagicMock()
        # Should NOT raise.
        engine = setup_engine(
            **{**_DEFAULTS, "console": console, "resume": str(resume)},
            force_resume=True,
        )
        result_ids = {s.id for s in engine.scenarios}
        # "alpha" was already in prior results -> skipped.
        assert "alpha" not in result_ids


# ---------------------------------------------------------------------------
# P1-16: backup file inherits the signed checksum
# ---------------------------------------------------------------------------

class TestBackupChecksumInherited:
    """_atomic_write_json's backup step must preserve whatever payload it
    copied, including any _checksum field. So the backup file is a
    byte-for-byte snapshot of the prior version — and thus carries the
    old version's signature, not a new one."""

    def test_backup_carries_prior_checksum(self, tmp_path):
        target = tmp_path / "out.json"
        entries_v1 = [{"scenario_id": "alpha", "health_score": 90, "verdict": "MAINTAINED"}]
        payload_v1 = {"results": entries_v1, "_checksum": compute_results_checksum(entries_v1)}
        _atomic_write_json(str(target), payload_v1)

        # Overwrite; backup should carry v1's checksum exactly.
        entries_v2 = [{"scenario_id": "alpha", "health_score": 70, "verdict": "DRIFTED"}]
        payload_v2 = {"results": entries_v2, "_checksum": compute_results_checksum(entries_v2)}
        _atomic_write_json(str(target), payload_v2)

        backup = tmp_path / "out.backup.json"
        assert backup.exists()
        backed_up = json.loads(backup.read_text())
        assert backed_up["_checksum"] == payload_v1["_checksum"]
        # And a re-verification of the backup still succeeds.
        assert compute_results_checksum(backed_up["results"]) == backed_up["_checksum"]


# ---------------------------------------------------------------------------
# P1-17: --skip-invalid flag
# ---------------------------------------------------------------------------

class TestSkipInvalidFlag:
    @patch("sapien_score.model_profiles.get_model_profile")
    @patch("sapien_score.engine.adapter.get_adapter")
    def test_skip_invalid_surfaces_skipped_on_config(self, mock_adapter, mock_profile, monkeypatch):
        """load_all_scenarios records skipped files in a module-level list;
        setup_engine must copy that list onto EngineConfig.skipped_scenarios."""
        from sapien_score.scenarios import loader as loader_mod

        def fake_loader(**kwargs):
            # Simulate what load_scenario_directory does on a validation
            # error when skip_invalid=True: populate _last_skipped_scenarios
            # and return whatever scenarios DID load cleanly.
            loader_mod._last_skipped_scenarios = [
                {"path": "/fake/bad.json", "reason": "validation: missing impact_tier"}
            ]
            return list(_CORPUS)

        # setup_engine resolves load_all_scenarios via
        # `from sapien_score.scenarios.loader import load_all_scenarios`
        # inside its body — patch the source module.
        monkeypatch.setattr(loader_mod, "load_all_scenarios", fake_loader)

        console = MagicMock()
        engine = setup_engine(
            **{**_DEFAULTS, "console": console},
            skip_invalid=True,
        )
        assert len(engine.skipped_scenarios) == 1
        assert engine.skipped_scenarios[0]["path"] == "/fake/bad.json"
        assert "validation" in engine.skipped_scenarios[0]["reason"]


# ---------------------------------------------------------------------------
# --replay path traversal hardening
# ---------------------------------------------------------------------------

class TestReplayPathTraversal:
    """The --replay path is a user-controlled file argument. A path
    containing ".." must be rejected regardless of whether it happens to
    resolve to a real file on disk — the previous implementation only
    validated the bundled-resource fallback branch."""

    @patch("sapien_score.scenarios.loader.load_all_scenarios", return_value=list(_CORPUS))
    def test_replay_with_parent_dir_component_rejected(self, mock_load, tmp_path):
        # Construct a ".." path; setup_engine must reject it BEFORE any
        # filesystem resolution, whether or not the file actually exists.
        hostile = str(tmp_path / ".." / "some_trace.jsonl")
        console = MagicMock()
        with pytest.raises(SystemExit) as exc_info:
            setup_engine(**{**_DEFAULTS, "console": console, "replay": hostile})
        assert exc_info.value.code == 1
        printed = " ".join(str(c) for c in console.print.call_args_list)
        assert "illegal" in printed.lower()

    @patch("sapien_score.scenarios.loader.load_all_scenarios", return_value=list(_CORPUS))
    def test_replay_with_existing_dotdot_still_rejected(self, mock_load, tmp_path):
        """Even when a ".." path resolves to an existing file, it must be
        rejected — this is the regression window the fix closes."""
        # Create a real file and point at it via ".."
        real = tmp_path / "real_trace.jsonl"
        real.write_text("{}\n", encoding="utf-8")
        # Path with ".." that resolves to the real file.
        subdir = tmp_path / "sub"
        subdir.mkdir()
        hostile = str(subdir / ".." / "real_trace.jsonl")
        console = MagicMock()
        with pytest.raises(SystemExit) as exc_info:
            setup_engine(**{**_DEFAULTS, "console": console, "replay": hostile})
        assert exc_info.value.code == 1
        printed = " ".join(str(c) for c in console.print.call_args_list)
        assert "illegal" in printed.lower()
