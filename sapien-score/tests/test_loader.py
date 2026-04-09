"""Tests for scenario loader."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.scenarios.loader import (
    load_scenario_file,
    load_scenario_directory,
    validate_scenario,
    VALID_DOMAINS,
    VALID_PRESSURE_TYPES,
)


SCENARIOS_DIR = Path(__file__).parent.parent / "scenarios"


class TestLoadAllScenarios:
    def test_scenarios_directory_exists(self):
        assert SCENARIOS_DIR.exists(), f"Scenarios directory not found: {SCENARIOS_DIR}"

    def test_load_all_yaml_files(self):
        yaml_files = list(SCENARIOS_DIR.rglob("*.yaml"))
        assert len(yaml_files) >= 50, f"Expected 50+ YAML files, found {len(yaml_files)}"

        loaded = 0
        errors = []
        for f in yaml_files:
            try:
                scenario = load_scenario_file(str(f))
                loaded += 1
            except Exception as e:
                errors.append(f"{f.name}: {e}")

        assert not errors, f"Failed to load {len(errors)} scenarios:\n" + "\n".join(errors[:10])
        assert loaded >= 50

    def test_all_scenarios_have_required_fields(self):
        yaml_files = list(SCENARIOS_DIR.rglob("*.yaml"))
        for f in yaml_files:
            scenario = load_scenario_file(str(f))
            assert scenario.id, f"{f.name}: missing id"
            assert scenario.domain, f"{f.name}: missing domain"
            assert scenario.title, f"{f.name}: missing title"
            assert scenario.opening_prompt, f"{f.name}: missing opening_prompt"
            assert len(scenario.escalations) >= 1, f"{f.name}: no escalations"

    def test_all_domains_valid(self):
        yaml_files = list(SCENARIOS_DIR.rglob("*.yaml"))
        for f in yaml_files:
            scenario = load_scenario_file(str(f))
            assert scenario.domain in VALID_DOMAINS, f"{f.name}: invalid domain '{scenario.domain}'"

    def test_domain_directories_present(self):
        expected = {"medical", "financial", "security", "legal", "hr", "education"}
        actual = {d.name for d in SCENARIOS_DIR.iterdir() if d.is_dir()}
        assert expected.issubset(actual), f"Missing domains: {expected - actual}"

    def test_load_directory(self):
        scenarios = load_scenario_directory(str(SCENARIOS_DIR))
        assert len(scenarios) >= 50, f"Expected 50+ scenarios, got {len(scenarios)}"

    def test_load_directory_by_domain(self):
        scenarios = load_scenario_directory(str(SCENARIOS_DIR), domain="medical")
        assert all(s.domain == "medical" for s in scenarios)
        assert len(scenarios) >= 5
