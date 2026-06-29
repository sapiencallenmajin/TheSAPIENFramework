# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

"""Tests for persona/memory injection and profile loading."""
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.engine.driver import _build_system_prompt
from sapien_score.personas.loader import (
    PersonaValidationError,
    _parse_profile,
    list_persona_profiles,
    load_persona_profile,
)


_VALID_PERSONA_YAML = (
    "id: test_good\n"
    "name: Test Persona\n"
    "role: Tester\n"
    "credentials: ''\n"
    "persona_text: some persona text\n"
    "memory_text: some memory text\n"
    "domain: medical\n"
)


class TestSystemPromptInjection:
    def test_no_injection(self):
        base = "You are a helpful assistant."
        result = _build_system_prompt(base)
        assert result == base

    def test_persona_only(self):
        base = "You are a helpful assistant."
        result = _build_system_prompt(base, persona_text="Dr. Sarah Chen, psychiatrist")
        assert result.startswith("[User Context]")
        assert "Dr. Sarah Chen, psychiatrist" in result
        assert result.endswith(base)
        assert "[Conversation History]" not in result

    def test_memory_only(self):
        base = "You are a helpful assistant."
        result = _build_system_prompt(base, memory_text="User discussed SSRI pharmacology.")
        assert result.startswith("[Conversation History]")
        assert "User discussed SSRI pharmacology." in result
        assert result.endswith(base)
        assert "[User Context]" not in result

    def test_combined_persona_first_then_memory(self):
        base = "You are a helpful assistant."
        result = _build_system_prompt(
            base,
            persona_text="Dr. Sarah Chen",
            memory_text="Prior sessions on SSRIs",
        )
        persona_pos = result.index("[User Context]")
        memory_pos = result.index("[Conversation History]")
        base_pos = result.index(base)
        # Persona comes first, then memory, then base
        assert persona_pos < memory_pos < base_pos

    def test_injection_preserves_base_prompt(self):
        base = "You are a helpful AI assistant. Answer questions clearly."
        result = _build_system_prompt(
            base,
            persona_text="Test persona",
            memory_text="Test memory",
        )
        assert base in result


class TestPersonaProfiles:
    def test_load_medical_professional(self):
        profile = load_persona_profile("medical_professional")
        assert profile.id == "medical_professional"
        assert profile.name == "Dr. Sarah Chen"
        assert profile.domain == "medical"
        assert len(profile.persona_text) > 0
        assert len(profile.memory_text) > 0

    def test_load_security_researcher(self):
        profile = load_persona_profile("security_researcher")
        assert profile.id == "security_researcher"
        assert profile.domain == "security"

    def test_load_financial_advisor(self):
        profile = load_persona_profile("financial_advisor")
        assert profile.id == "financial_advisor"
        assert profile.domain == "financial"

    def test_load_legal_professional(self):
        profile = load_persona_profile("legal_professional")
        assert profile.id == "legal_professional"
        assert profile.domain == "legal"

    def test_load_concerned_parent(self):
        profile = load_persona_profile("concerned_parent")
        assert profile.id == "concerned_parent"
        assert profile.domain == "medical"

    def test_load_graduate_student(self):
        profile = load_persona_profile("graduate_student")
        assert profile.id == "graduate_student"
        assert profile.domain == "security"

    def test_list_all_profiles(self):
        profiles = list_persona_profiles()
        assert len(profiles) == 6
        ids = {p.id for p in profiles}
        assert "medical_professional" in ids
        assert "security_researcher" in ids
        assert "financial_advisor" in ids
        assert "legal_professional" in ids
        assert "concerned_parent" in ids
        assert "graduate_student" in ids

    def test_nonexistent_profile_raises(self):
        import pytest
        with pytest.raises(FileNotFoundError):
            load_persona_profile("nonexistent_profile")

    def test_all_profiles_have_required_fields(self):
        profiles = list_persona_profiles()
        for p in profiles:
            assert p.id, f"Profile missing id"
            assert p.name, f"Profile {p.id} missing name"
            assert p.role, f"Profile {p.id} missing role"
            assert p.persona_text, f"Profile {p.id} missing persona_text"
            assert p.memory_text, f"Profile {p.id} missing memory_text"
            assert p.domain, f"Profile {p.id} missing domain"


class TestPersonaValidationErrorGuards:
    """_parse_profile must reject malformed-but-parseable YAML input with
    a specific PersonaValidationError rather than propagating AttributeError
    from data.get(...) calls."""

    def test_parse_none_raises(self):
        with pytest.raises(PersonaValidationError, match="empty"):
            _parse_profile(None)

    def test_parse_list_raises(self):
        with pytest.raises(PersonaValidationError, match="mapping"):
            _parse_profile([{"id": "x"}])

    def test_parse_string_raises(self):
        with pytest.raises(PersonaValidationError, match="mapping"):
            _parse_profile("just a string")


class TestListPersonaProfilesResilience:
    """list_persona_profiles must warn-and-skip on malformed persona files
    so list_info / CLI "available profiles" output never crashes on a bad
    file in the user's personas directory."""

    def test_empty_persona_file_is_skipped(self, tmp_path, monkeypatch, caplog):
        (tmp_path / "good.yaml").write_text(_VALID_PERSONA_YAML, encoding="utf-8")
        (tmp_path / "empty.yaml").write_text("", encoding="utf-8")
        monkeypatch.setenv("SAPIEN_PERSONAS", str(tmp_path))

        with caplog.at_level(logging.WARNING, logger="sapien_score.personas.loader"):
            profiles = list_persona_profiles()

        assert len(profiles) == 1
        assert profiles[0].id == "test_good"
        assert any("empty" in rec.message for rec in caplog.records)

    def test_list_root_persona_file_is_skipped(
        self, tmp_path, monkeypatch, caplog
    ):
        (tmp_path / "good.yaml").write_text(_VALID_PERSONA_YAML, encoding="utf-8")
        (tmp_path / "list_root.yaml").write_text(
            "- id: foo\n- id: bar\n", encoding="utf-8"
        )
        monkeypatch.setenv("SAPIEN_PERSONAS", str(tmp_path))

        with caplog.at_level(logging.WARNING, logger="sapien_score.personas.loader"):
            profiles = list_persona_profiles()

        assert len(profiles) == 1
        assert profiles[0].id == "test_good"
        assert any("mapping" in rec.message for rec in caplog.records)

    def test_load_persona_profile_propagates_validation_error(
        self, tmp_path, monkeypatch
    ):
        """When the user explicitly asks for a profile by id, a malformed
        file with that filename should surface the error (not silently
        fall back to 'not found')."""
        (tmp_path / "broken.yaml").write_text("", encoding="utf-8")
        monkeypatch.setenv("SAPIEN_PERSONAS", str(tmp_path))

        with pytest.raises(PersonaValidationError, match="empty"):
            load_persona_profile("broken")

    def test_persona_search_survives_bad_sibling(
        self, tmp_path, monkeypatch, caplog
    ):
        """Regression: the search-by-id loop in load_persona_profile used
        to crash on the first malformed YAML file it encountered,
        poisoning lookups for every other profile in the same directory.

        Setup: a good profile with id 'wanted' and a broken sibling
        that gets alphabetized first. Before the fix, opening
        'alpha_broken.yaml' blew up with a yaml.YAMLError or
        AttributeError before the loop ever reached 'good.yaml'.
        """
        # Name the bad file so it sorts BEFORE the good one — the
        # loop iterates in sorted order so the broken file is hit first.
        (tmp_path / "alpha_broken.yaml").write_text(
            "{this is: not valid yaml:::\n", encoding="utf-8"
        )
        (tmp_path / "good.yaml").write_text(
            "id: wanted\n"
            "name: Wanted Profile\n"
            "role: Target\n"
            "credentials: ''\n"
            "persona_text: x\n"
            "memory_text: y\n"
            "domain: medical\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("SAPIEN_PERSONAS", str(tmp_path))

        with caplog.at_level(logging.WARNING, logger="sapien_score.personas.loader"):
            profile = load_persona_profile("wanted")

        assert profile.id == "wanted"
        assert profile.name == "Wanted Profile"
        # The broken sibling must have produced a warning but not a crash.
        assert any(
            "alpha_broken" in rec.message for rec in caplog.records
        ), "expected a warning log entry for the broken sibling file"

    def test_persona_search_survives_list_root_sibling(
        self, tmp_path, monkeypatch, caplog
    ):
        """Same shape as above but with a list-root YAML file (valid
        YAML, wrong shape). Used to AttributeError on ``data.get(...)``."""
        (tmp_path / "alpha_list.yaml").write_text(
            "- id: first\n- id: second\n", encoding="utf-8"
        )
        (tmp_path / "good.yaml").write_text(
            "id: wanted\n"
            "name: Wanted\n"
            "role: Target\n"
            "credentials: ''\n"
            "persona_text: x\n"
            "memory_text: y\n"
            "domain: medical\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("SAPIEN_PERSONAS", str(tmp_path))

        profile = load_persona_profile("wanted")
        assert profile.id == "wanted"

    def test_explicit_profile_yaml_syntax_error_wraps_as_validation_error(
        self, tmp_path, monkeypatch
    ):
        """Regression: when a user explicitly requests a profile by id and
        the file with that name has a YAML *syntax* error, the exact
        filename match path used to propagate raw ``yaml.YAMLError``,
        which was not in the memory_delta/scan except tuple and crashed
        the CLI with an uncaught traceback.

        The fix wraps the exact-match read in a narrow except and
        re-raises as PersonaValidationError with ``from e`` chaining.
        """
        (tmp_path / "medical.yaml").write_text(
            "id: medical\n  bad: indent: here\n", encoding="utf-8"
        )
        monkeypatch.setenv("SAPIEN_PERSONAS", str(tmp_path))

        with pytest.raises(
            PersonaValidationError, match="failed to load persona"
        ) as excinfo:
            load_persona_profile("medical")
        # Underlying cause should be chained for debuggability.
        assert excinfo.value.__cause__ is not None

    def test_explicit_profile_non_utf8_bytes_wraps_as_validation_error(
        self, tmp_path, monkeypatch
    ):
        """A file saved as cp1252 / UTF-16 instead of UTF-8 used to raise
        UnicodeDecodeError, which was also not in the CLI except tuple."""
        # 0xFF 0xFE 0x00 0x00 is a UTF-32 LE BOM — not valid UTF-8 at all.
        (tmp_path / "corrupt.yaml").write_bytes(
            b"\xff\xfe\x00\x00id: corrupt\n"
        )
        monkeypatch.setenv("SAPIEN_PERSONAS", str(tmp_path))

        with pytest.raises(
            PersonaValidationError, match="failed to load persona"
        ):
            load_persona_profile("corrupt")
