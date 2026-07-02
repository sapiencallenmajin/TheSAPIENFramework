# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC
"""Tests for the fail-loud CLI hardening: --resume implies --all, and the
preflight subcommand."""
from click.testing import CliRunner

from sapien_score.cli import main
from sapien_score.commands.scan_orchestration import resume_implies_run_all


# --- --resume implies --all -------------------------------------------------

def test_resume_with_no_filter_implies_run_all():
    assert resume_implies_run_all(
        run_all=False, resume="run.json", domain=None, domains=None, scenario_ids=None
    ) is True


def test_resume_does_not_override_explicit_filters():
    # An explicit filter alongside --resume is respected, not widened to all.
    assert resume_implies_run_all(False, "run.json", "medical", None, None) is False
    assert resume_implies_run_all(False, "run.json", None, "medical,legal", None) is False
    assert resume_implies_run_all(False, "run.json", None, None, "sapien.x.y.v1") is False
    assert resume_implies_run_all(True, "run.json", None, None, None) is False


def test_no_resume_no_filter_stays_false():
    # Without --resume, a bare invocation must still hit the no-filter guard.
    assert resume_implies_run_all(False, None, None, None, None) is False


# --- preflight subcommand ---------------------------------------------------

def test_preflight_is_registered():
    assert "preflight" in main.commands


def _clear_provider_env(monkeypatch):
    for var in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY",
                "AWS_ACCESS_KEY_ID"):
        monkeypatch.delenv(var, raising=False)
    # ~/.aws/credentials may exist on a dev box; force the Nova check to FAIL.
    import sapien_score.commands.preflight as pf
    monkeypatch.setattr(pf.os.path, "exists", lambda p: False)


def test_preflight_fails_loud_when_keys_missing(monkeypatch):
    _clear_provider_env(monkeypatch)
    result = CliRunner().invoke(main, ["preflight", "--no-seats"])
    assert result.exit_code == 1
    assert "PREFLIGHT: FAIL" in result.output
    assert "MISSING" in result.output


def test_preflight_no_seats_skips_pinging(monkeypatch):
    # --no-seats must not attempt any provider ping (no 'Seat liveness' row).
    _clear_provider_env(monkeypatch)
    result = CliRunner().invoke(main, ["preflight", "--no-seats"])
    assert "Seat liveness" not in result.output
