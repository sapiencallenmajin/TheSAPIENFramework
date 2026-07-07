# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

"""Tests for scenario packs — manifest parsing, resolution, CLI wiring."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from sapien_score.scenarios.loader import Scenario
from sapien_score.scenarios.packs import (
    PackError,
    PackManifest,
    list_packs,
    load_pack,
    packs_dir,
    parse_pack_manifest,
    resolve_pack,
)


def _scenario(sid: str, domain: str) -> Scenario:
    return Scenario(
        id=sid, version="1.0", domain=domain, title=sid, description="d",
        pressure_type="persistence", severity=3, max_turns=8,
        system_prompt="s", opening_prompt="o", escalations=[],
    )


CORPUS = [
    _scenario("sapien.medical.chest_pain.v1", "medical"),
    _scenario("sapien.medical.meds.v1", "medical"),
    _scenario("sapien.tax.home_office_abuse.v1", "tax"),
    _scenario("sapien.tax.payroll_tax_diversion.v1", "tax"),
    _scenario("sapien.healthcare_admin.upcoding_pressure.v1", "healthcare_admin"),
]


# ---- Manifest parsing ----

class TestParseManifest:
    def test_parses_valid_manifest(self):
        m = parse_pack_manifest({
            "name": "demo", "description": "a pack", "version": "2",
            "members": ["sapien.medical.meds.v1", "domain:tax"],
        })
        assert m.name == "demo"
        assert m.version == "2"
        assert m.members == ["sapien.medical.meds.v1", "domain:tax"]

    @pytest.mark.parametrize("missing", ["name", "description", "version"])
    def test_missing_string_field_rejected(self, missing):
        data = {"name": "x", "description": "y", "version": "1",
                "members": ["a"]}
        del data[missing]
        with pytest.raises(PackError, match=missing):
            parse_pack_manifest(data)

    @pytest.mark.parametrize("members", [None, [], "notalist", [1], [""]])
    def test_bad_members_rejected(self, members):
        with pytest.raises(PackError, match="members"):
            parse_pack_manifest({
                "name": "x", "description": "y", "version": "1",
                "members": members,
            })

    def test_non_dict_root_rejected(self):
        with pytest.raises(PackError, match="root"):
            parse_pack_manifest(["not", "a", "dict"])


# ---- Loading from disk ----

class TestLoadPack:
    def test_unknown_pack_lists_available(self, tmp_path):
        (tmp_path / "real.json").write_text(json.dumps({
            "name": "real", "description": "d", "version": "1",
            "members": ["a"],
        }), encoding="utf-8")
        with pytest.raises(PackError, match="real"):
            load_pack("typo", directory=tmp_path)

    def test_malformed_json_is_pack_error(self, tmp_path):
        (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(PackError, match="bad"):
            load_pack("bad", directory=tmp_path)

    def test_list_packs_empty_dir(self, tmp_path):
        assert list_packs(directory=tmp_path / "nope") == []

    def test_list_packs_malformed_json_is_pack_error(self, tmp_path):
        """One broken manifest must be a PackError, not a raw traceback."""
        (tmp_path / "good.json").write_text(json.dumps({
            "name": "good", "description": "d", "version": "1",
            "members": ["a"],
        }), encoding="utf-8")
        (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(PackError, match="bad"):
            list_packs(directory=tmp_path)

    def test_name_must_match_filename_stem_on_load(self, tmp_path):
        (tmp_path / "alt.json").write_text(json.dumps({
            "name": "core", "description": "d", "version": "1",
            "members": ["a"],
        }), encoding="utf-8")
        with pytest.raises(PackError, match="does not match its filename"):
            load_pack("alt", directory=tmp_path)

    def test_name_must_match_filename_stem_on_list(self, tmp_path):
        """alt.json claiming name 'core' would duplicate/shadow the real
        core pack in listings and scan provenance — rejected loudly."""
        (tmp_path / "alt.json").write_text(json.dumps({
            "name": "core", "description": "d", "version": "1",
            "members": ["a"],
        }), encoding="utf-8")
        with pytest.raises(PackError, match="does not match its filename"):
            list_packs(directory=tmp_path)

    def test_list_packs_sorted(self, tmp_path):
        for name in ("zeta", "alpha"):
            (tmp_path / f"{name}.json").write_text(json.dumps({
                "name": name, "description": "d", "version": "1",
                "members": ["a"],
            }), encoding="utf-8")
        assert [m.name for m in list_packs(directory=tmp_path)] == ["alpha", "zeta"]


# ---- Resolution ----

class TestResolvePack:
    def _manifest(self, members):
        return PackManifest(name="t", description="d", version="1",
                            members=members)

    def test_exact_id(self):
        r = resolve_pack(self._manifest(["sapien.medical.meds.v1"]), CORPUS)
        assert r.scenario_ids == ["sapien.medical.meds.v1"]
        assert r.unresolved_members == []

    def test_domain_member(self):
        r = resolve_pack(self._manifest(["domain:tax"]), CORPUS)
        assert r.scenario_ids == [
            "sapien.tax.home_office_abuse.v1",
            "sapien.tax.payroll_tax_diversion.v1",
        ]

    def test_glob_member(self):
        r = resolve_pack(self._manifest(["sapien.medical.*"]), CORPUS)
        assert r.scenario_ids == [
            "sapien.medical.chest_pain.v1", "sapien.medical.meds.v1",
        ]

    def test_deduplicates_overlapping_members(self):
        r = resolve_pack(
            self._manifest(["domain:medical", "sapien.medical.meds.v1"]),
            CORPUS,
        )
        assert r.scenario_ids.count("sapien.medical.meds.v1") == 1

    def test_unresolved_members_reported(self):
        r = resolve_pack(
            self._manifest([
                "sapien.medical.meds.v1",
                "sapien.nope.missing.v1",
                "domain:atlantis",
                "sapien.ghost.*",
            ]),
            CORPUS,
        )
        assert r.scenario_ids == ["sapien.medical.meds.v1"]
        assert r.unresolved_members == [
            "sapien.nope.missing.v1", "domain:atlantis", "sapien.ghost.*",
        ]


# ---- Shipped starter packs resolve against the real corpus ----

class TestShippedPacks:
    def test_packs_dir_has_starter_packs(self):
        names = {m.name for m in list_packs()}
        assert {"quick", "core", "healthcare"} <= names

    @pytest.mark.parametrize("name", ["quick", "core", "healthcare"])
    def test_every_member_resolves(self, name):
        from sapien_score.scenarios.loader import load_all_scenarios
        manifest = load_pack(name)
        resolution = resolve_pack(manifest, load_all_scenarios(collection="sapien"))
        assert resolution.unresolved_members == []
        assert resolution.scenario_ids

    def test_quick_is_small(self):
        from sapien_score.scenarios.loader import load_all_scenarios
        r = resolve_pack(load_pack("quick"), load_all_scenarios(collection="sapien"))
        assert 5 <= len(r.scenario_ids) <= 15


# ---- CLI wiring ----

class TestScanPackOption:
    @pytest.mark.parametrize("conflict", [
        ["--scenario-ids", "sapien.medical.meds.v1"],
        ["--all"],
        ["--domain", "medical"],
        ["--domains", "medical,tax"],
        ["--authorship", "llm-reviewed"],
        ["--audience", "benchmark"],
    ])
    def test_pack_mutually_exclusive(self, conflict):
        from sapien_score.commands.scan import scan
        result = CliRunner().invoke(
            scan, ["--model", "test/model", "--pack", "quick", *conflict],
        )
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output

    def test_unknown_pack_errors(self):
        from sapien_score.commands.scan import scan
        result = CliRunner().invoke(
            scan, ["--model", "test/model", "--pack", "nonexistent_pack"],
        )
        assert result.exit_code == 1
        assert "Unknown pack" in result.output

    def test_unresolvable_member_errors(self, tmp_path, monkeypatch):
        from sapien_score.commands.scan import scan
        pack_root = tmp_path / "packs"
        pack_root.mkdir()
        (pack_root / "stale.json").write_text(json.dumps({
            "name": "stale", "description": "d", "version": "1",
            "members": ["sapien.medical.meds.v1", "sapien.removed.gone.v1"],
        }), encoding="utf-8")
        real_root = packs_dir().parent
        monkeypatch.setattr(
            "sapien_score.scenarios.packs.packs_dir", lambda: pack_root,
        )
        result = CliRunner().invoke(
            scan, ["--model", "test/model", "--pack", "stale"],
        )
        assert result.exit_code == 1
        assert "sapien.removed.gone.v1" in result.output
        assert real_root.is_dir()  # sanity: corpus root untouched

    def test_pack_resolution_prints_count_and_estimates(self):
        """--pack + --estimate exercises resolution through the real
        --scenario-ids machinery without any API calls."""
        from sapien_score.commands.scan import scan
        result = CliRunner().invoke(
            scan,
            ["--model", "test/model", "--pack", "quick", "--estimate",
             "--display", "plain"],
        )
        assert result.exit_code == 0, result.output
        assert "Pack 'quick'" in result.output
        assert "resolved 10 scenario(s)" in result.output
        assert "Resolved 10 scenario(s) in scope." in result.output


class TestPacksCommand:
    def test_lists_shipped_packs(self):
        from sapien_score.commands.list_info import packs as packs_cmd
        result = CliRunner().invoke(packs_cmd, [])
        assert result.exit_code == 0, result.output
        for name in ("quick", "core", "healthcare"):
            assert name in result.output

    def test_flags_stale_members(self, tmp_path, monkeypatch):
        from sapien_score.commands.list_info import packs as packs_cmd
        pack_root = tmp_path / "packs"
        pack_root.mkdir()
        (pack_root / "stale.json").write_text(json.dumps({
            "name": "stale", "description": "d", "version": "1",
            "members": ["sapien.removed.gone.v1"],
        }), encoding="utf-8")
        monkeypatch.setattr(
            "sapien_score.scenarios.packs.packs_dir", lambda: pack_root,
        )
        result = CliRunner().invoke(packs_cmd, [])
        assert result.exit_code == 0, result.output
        assert "sapien.removed.gone.v1" in result.output

    def test_malformed_manifest_is_clean_cli_error(self, tmp_path, monkeypatch):
        """A broken pack file must produce the red PackError message,
        not an unhandled JSONDecodeError traceback."""
        pack_root = tmp_path / "packs"
        pack_root.mkdir()
        (pack_root / "bad.json").write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(
            "sapien_score.scenarios.packs.packs_dir", lambda: pack_root,
        )
        from sapien_score.commands.list_info import packs as packs_cmd
        result = CliRunner().invoke(packs_cmd, [])
        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
        assert "Failed to read pack file" in result.output


class TestListPackOption:
    def test_list_filters_to_pack(self):
        from sapien_score.commands.list_info import list_scenarios
        result = CliRunner().invoke(list_scenarios, ["--pack", "quick"])
        assert result.exit_code == 0, result.output
        assert "10 scenarios total" in result.output

    def test_list_unknown_pack_errors(self):
        from sapien_score.commands.list_info import list_scenarios
        result = CliRunner().invoke(list_scenarios, ["--pack", "nope"])
        assert result.exit_code == 1
        assert "Unknown pack" in result.output
