"""Tests for --scenario-ids filtering in setup_engine."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.commands.scan_orchestration import setup_engine


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
