"""Tests for persona/memory injection and profile loading."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.engine.driver import _build_system_prompt
from sapien_score.personas.loader import load_persona_profile, list_persona_profiles


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
