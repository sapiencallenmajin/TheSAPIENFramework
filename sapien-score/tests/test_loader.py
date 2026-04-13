"""Tests for scenario loader."""
import json
import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.scenarios.loader import (
    ScenarioValidationError,
    load_all_scenarios,
    load_scenario_directory,
    load_scenario_file,
    load_scenario_from_dict,
    validate_scenario,
    _scenario_cache,
    VALID_COLLECTIONS,
    VALID_DOMAINS,
    VALID_PRESSURE_TYPES,
)
from sapien_score.commands._shared import check_cross_family_judge


# A minimal valid scenario dict, used as a fixture for the malformed-input
# tests below. All required fields are present so that load_scenario_from_dict
# succeeds unless we deliberately break one thing.
_VALID_SCENARIO = {
    "id": "test_good",
    "domain": "medical",
    "title": "Good Scenario",
    "description": "ok",
    "opening_prompt": "hi",
    "escalations": [{"turn": 1, "prompt": "tell me more"}],
}

_VALID_JSON = json.dumps(_VALID_SCENARIO)


# After restructure: scenarios live under scenarios/sapien/{domain}/
SCENARIOS_ROOT = Path(__file__).parent.parent / "scenarios"
SAPIEN_DIR = SCENARIOS_ROOT / "sapien"


class TestLoadAllScenarios:
    def test_scenarios_directory_exists(self):
        assert SAPIEN_DIR.exists(), f"Sapien directory not found: {SAPIEN_DIR}"

    def test_load_all_scenario_files(self):
        scenario_files = list(SAPIEN_DIR.rglob("*.json"))
        assert len(scenario_files) >= 50, f"Expected 50+ JSON files, found {len(scenario_files)}"

        loaded = 0
        errors = []
        for f in scenario_files:
            try:
                scenario = load_scenario_file(str(f))
                loaded += 1
            except Exception as e:
                errors.append(f"{f.name}: {e}")

        assert not errors, f"Failed to load {len(errors)} scenarios:\n" + "\n".join(errors[:10])
        assert loaded >= 50

    def test_all_scenarios_have_required_fields(self):
        scenario_files = list(SAPIEN_DIR.rglob("*.json"))
        for f in scenario_files:
            scenario = load_scenario_file(str(f))
            assert scenario.id, f"{f.name}: missing id"
            assert scenario.domain, f"{f.name}: missing domain"
            assert scenario.title, f"{f.name}: missing title"
            assert scenario.opening_prompt, f"{f.name}: missing opening_prompt"
            assert len(scenario.escalations) >= 1, f"{f.name}: no escalations"

    def test_all_domains_valid(self):
        scenario_files = list(SAPIEN_DIR.rglob("*.json"))
        for f in scenario_files:
            scenario = load_scenario_file(str(f))
            assert scenario.domain in VALID_DOMAINS, f"{f.name}: invalid domain '{scenario.domain}'"

    def test_domain_directories_present(self):
        expected = {"medical", "financial", "security", "legal", "hr", "education"}
        actual = {d.name for d in SAPIEN_DIR.iterdir() if d.is_dir()}
        assert expected.issubset(actual), f"Missing domains: {expected - actual}"

    def test_load_directory(self):
        scenarios = load_scenario_directory(str(SAPIEN_DIR))
        assert len(scenarios) >= 50, f"Expected 50+ scenarios, got {len(scenarios)}"

    def test_load_directory_by_domain(self):
        scenarios = load_scenario_directory(str(SAPIEN_DIR), domain="medical")
        assert all(s.domain == "medical" for s in scenarios)
        assert len(scenarios) >= 5


class TestLoadFromDictGuards:
    """Malformed-but-parseable input should raise ScenarioValidationError,
    not bypass validation and crash with TypeError/AttributeError. These
    guards let load_scenario_directory warn-and-continue instead of dying
    on the first bad file in a user's scenarios directory."""

    def test_none_raises_validation_error(self):
        with pytest.raises(ScenarioValidationError, match="empty"):
            load_scenario_from_dict(None)

    def test_list_root_raises_validation_error(self):
        with pytest.raises(ScenarioValidationError, match="mapping"):
            load_scenario_from_dict([{"id": "x"}])

    def test_string_root_raises_validation_error(self):
        with pytest.raises(ScenarioValidationError, match="mapping"):
            load_scenario_from_dict("just a string")

    def test_integer_root_raises_validation_error(self):
        with pytest.raises(ScenarioValidationError, match="mapping"):
            load_scenario_from_dict(42)

    def test_non_numeric_hold_variants_key_raises(self):
        data = dict(_VALID_SCENARIO)
        data["hold_variants"] = {"abc": ["some variant"]}
        with pytest.raises(ScenarioValidationError, match="hold_variants"):
            load_scenario_from_dict(data)

    def test_severity_string_raises_validation_error(self):
        """``severity: "high"`` must surface as a ScenarioValidationError
        with a clear message, not a raw TypeError."""
        data = dict(_VALID_SCENARIO)
        data["severity"] = "high"
        with pytest.raises(ScenarioValidationError, match="severity must be a number"):
            load_scenario_from_dict(data)

    def test_max_turns_string_raises_validation_error(self):
        """Same shape as the severity typo. ``max_turns: "eight"`` used to
        crash with TypeError on the ``< 4`` comparison."""
        data = dict(_VALID_SCENARIO)
        data["max_turns"] = "eight"
        with pytest.raises(ScenarioValidationError, match="max_turns must be a number"):
            load_scenario_from_dict(data)

    def test_escalations_not_list_raises_validation_error(self):
        """An integer or string ``escalations`` field used to trip
        TypeError on ``enumerate(...)`` or AttributeError on
        ``esc.get(...)``. Must surface as ScenarioValidationError."""
        data = dict(_VALID_SCENARIO)
        data["escalations"] = 42
        with pytest.raises(ScenarioValidationError, match="escalations must be a list"):
            load_scenario_from_dict(data)

    def test_escalations_string_raises_validation_error(self):
        data = dict(_VALID_SCENARIO)
        data["escalations"] = "not a list"
        with pytest.raises(ScenarioValidationError, match="escalations must be a list"):
            load_scenario_from_dict(data)

    def test_escalation_item_not_dict_raises_validation_error(self):
        """A list of strings where a list of escalation dicts is
        expected used to AttributeError on ``esc.get(...)``."""
        data = dict(_VALID_SCENARIO)
        data["escalations"] = ["just a string"]
        with pytest.raises(ScenarioValidationError, match="must be a mapping"):
            load_scenario_from_dict(data)

    def test_numeric_string_hold_variants_key_is_accepted(self):
        """A hold_variants key stored as "3" must coerce cleanly
        — only truly non-numeric keys fail."""
        data = dict(_VALID_SCENARIO)
        data["hold_variants"] = {"3": ["variant"]}
        scenario = load_scenario_from_dict(data)
        assert scenario.hold_variants == {3: ["variant"]}


class TestLoadDirectoryResilience:
    """load_scenario_directory must warn-and-skip on malformed files rather
    than crash the whole scan. These are exactly the kinds of bugs the
    public tool must not generate user-facing tracebacks for."""

    def test_empty_json_file_is_skipped(self, tmp_path, caplog):
        (tmp_path / "good.json").write_text(_VALID_JSON, encoding="utf-8")
        (tmp_path / "empty.json").write_text("", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="sapien_score.scenarios.loader"):
            scenarios = load_scenario_directory(str(tmp_path))

        assert len(scenarios) == 1
        assert scenarios[0].id == "test_good"
        assert any("empty" in rec.message for rec in caplog.records)

    def test_list_root_json_is_skipped(self, tmp_path, caplog):
        (tmp_path / "good.json").write_text(_VALID_JSON, encoding="utf-8")
        (tmp_path / "list_root.json").write_text(
            '[{"id": "foo"}, {"id": "bar"}]', encoding="utf-8"
        )

        with caplog.at_level(logging.WARNING, logger="sapien_score.scenarios.loader"):
            scenarios = load_scenario_directory(str(tmp_path))

        assert len(scenarios) == 1
        assert scenarios[0].id == "test_good"
        assert any("mapping" in rec.message for rec in caplog.records)

    def test_malformed_hold_variants_is_skipped(self, tmp_path, caplog):
        (tmp_path / "good.json").write_text(_VALID_JSON, encoding="utf-8")
        bad = dict(_VALID_SCENARIO)
        bad["hold_variants"] = {"abc": ["variant one"]}
        (tmp_path / "bad_hold.json").write_text(
            json.dumps(bad), encoding="utf-8"
        )

        with caplog.at_level(logging.WARNING, logger="sapien_score.scenarios.loader"):
            scenarios = load_scenario_directory(str(tmp_path))

        assert len(scenarios) == 1
        assert scenarios[0].id == "test_good"
        assert any("hold_variants" in rec.message for rec in caplog.records)

    def test_malformed_json_file_is_skipped(self, tmp_path, caplog):
        (tmp_path / "good.json").write_text(_VALID_JSON, encoding="utf-8")
        (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="sapien_score.scenarios.loader"):
            scenarios = load_scenario_directory(str(tmp_path))

        assert len(scenarios) == 1
        assert scenarios[0].id == "test_good"
        assert any("broken.json" in rec.message for rec in caplog.records)


class TestLoadAllScenariosFn:
    """Tests for the collection-aware load_all_scenarios()."""

    def setup_method(self):
        _scenario_cache.clear()

    def test_loader_finds_sapien_scenarios(self):
        """Default load_all_scenarios returns scenarios from sapien/."""
        scenarios = load_all_scenarios()
        assert len(scenarios) >= 50, f"Expected 50+ sapien scenarios, got {len(scenarios)}"

    def test_collection_filter_sapien(self):
        scenarios = load_all_scenarios(collection="sapien")
        assert len(scenarios) >= 50

    def test_collection_filter_community_empty(self):
        """Community collection is empty by default."""
        scenarios = load_all_scenarios(collection="community")
        assert len(scenarios) == 0

    def test_collection_filter_redteam_empty(self):
        """Red-team collection is empty by default."""
        scenarios = load_all_scenarios(collection="red-team")
        assert len(scenarios) == 0

    def test_default_excludes_custom(self):
        """Default collection is sapien only, not custom."""
        scenarios = load_all_scenarios()
        # Verify we get sapien scenarios, not an accidental all-collection load
        assert len(scenarios) >= 50

    def test_collection_all_includes_sapien(self):
        scenarios = load_all_scenarios(collection="all")
        assert len(scenarios) >= 50

    def test_domain_filter(self):
        scenarios = load_all_scenarios(domain="medical")
        assert all(s.domain == "medical" for s in scenarios)
        assert len(scenarios) >= 5

    def test_audience_filter(self):
        scenarios = load_all_scenarios(audience="general")
        assert all(s.audience == "general" for s in scenarios)

    def test_authorship_filter_no_match(self):
        """No built-in scenarios have authorship set, so this returns empty."""
        scenarios = load_all_scenarios(authorship="llm")
        assert len(scenarios) == 0

    def test_custom_scenarios_dir(self, tmp_path):
        """scenarios_dir override loads from a custom directory."""
        (tmp_path / "test.json").write_text(_VALID_JSON, encoding="utf-8")
        scenarios = load_all_scenarios(scenarios_dir=str(tmp_path))
        assert len(scenarios) == 1
        assert scenarios[0].id == "test_good"

    def test_cache_is_populated(self):
        _scenario_cache.clear()
        load_all_scenarios()
        assert len(_scenario_cache) > 0


class TestCrossFamilyJudgeWarning:
    """Tests for the cross-family judge warning."""

    def test_same_provider_warns(self):
        warning = check_cross_family_judge(
            "anthropic/claude-sonnet-4-20250514",
            "anthropic/claude-sonnet-4-20250514",
        )
        assert warning is not None
        assert "anthropic" in warning
        assert "Same-family" in warning

    def test_different_provider_no_warning(self):
        warning = check_cross_family_judge(
            "anthropic/claude-sonnet-4-20250514",
            "openai/gpt-4o",
        )
        assert warning is None

    def test_no_judge_no_warning(self):
        warning = check_cross_family_judge(
            "anthropic/claude-sonnet-4-20250514",
            None,
        )
        assert warning is None

    def test_no_prefix_no_warning(self):
        """Models without a provider prefix don't trigger the warning."""
        warning = check_cross_family_judge("gpt-4o", "gpt-4o")
        assert warning is None

    def test_vertex_same_family_warns(self):
        warning = check_cross_family_judge(
            "vertex_ai/gemini-pro",
            "vertex_ai/gemini-ultra",
        )
        assert warning is not None
        assert "vertex_ai" in warning
