"""Tests for hunter/memory.py — Elephantasm integration layer.

All tests mock the Elephantasm SDK. No real API calls are made.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def anima_cache(tmp_path):
    """Provide a temp path for the Anima ID cache file."""
    return tmp_path / "animas.json"


@pytest.fixture
def mock_client():
    """Provide a mocked Elephantasm client instance."""
    client = MagicMock()
    client.close = MagicMock()
    return client


def _make_overseer_bridge(mock_client):
    """Create an OverseerMemoryBridge with a mocked client."""
    from hunter.memory import OverseerMemoryBridge

    with patch("hunter.memory.Elephantasm", return_value=mock_client):
        with patch("hunter.memory.AnimaManager.get_anima_id", return_value="overseer-id"):
            bridge = OverseerMemoryBridge()
    return bridge


def _make_hunter_bridge(mock_client):
    """Create a HunterMemoryBridge with a mocked client."""
    from hunter.memory import HunterMemoryBridge

    with patch("hunter.memory.Elephantasm", return_value=mock_client):
        with patch("hunter.memory.AnimaManager.get_anima_id", return_value="hunter-id"):
            bridge = HunterMemoryBridge()
    bridge.set_session("hunt-001")
    return bridge


# ---------------------------------------------------------------------------
# AnimaManager tests
# ---------------------------------------------------------------------------

class TestAnimaManager:

    def test_ensure_animas_creates_both(self, anima_cache):
        """ensure_animas() creates both Animas and caches their IDs."""
        from hunter.memory import AnimaManager

        mock_client = MagicMock()
        overseer_anima = MagicMock()
        overseer_anima.id = "overseer-uuid-123"
        hunter_anima = MagicMock()
        hunter_anima.id = "hunter-uuid-456"
        mock_client.create_anima.side_effect = [overseer_anima, hunter_anima]

        with patch("hunter.memory.Elephantasm", return_value=mock_client):
            with patch("hunter.memory._HAS_ELEPHANTASM", True):
                result = AnimaManager.ensure_animas(cache_path=anima_cache)

        assert result["hermes-overseer"] == "overseer-uuid-123"
        assert result["hermes-hunter"] == "hunter-uuid-456"
        assert mock_client.create_anima.call_count == 2
        mock_client.close.assert_called_once()

        # Verify cache file was written
        cached = json.loads(anima_cache.read_text())
        assert cached["hermes-overseer"] == "overseer-uuid-123"

    def test_ensure_animas_uses_cache(self, anima_cache):
        """If both IDs are cached, no API calls are made."""
        from hunter.memory import AnimaManager

        anima_cache.write_text(json.dumps({
            "hermes-overseer": "cached-o",
            "hermes-hunter": "cached-h",
        }))

        result = AnimaManager.ensure_animas(cache_path=anima_cache)
        assert result["hermes-overseer"] == "cached-o"
        assert result["hermes-hunter"] == "cached-h"

    def test_ensure_animas_partial_cache(self, anima_cache):
        """If only one Anima is cached, only the missing one is created."""
        from hunter.memory import AnimaManager

        anima_cache.write_text(json.dumps({"hermes-overseer": "cached-o"}))

        mock_client = MagicMock()
        hunter_anima = MagicMock()
        hunter_anima.id = "new-hunter-id"
        mock_client.create_anima.return_value = hunter_anima

        with patch("hunter.memory.Elephantasm", return_value=mock_client):
            with patch("hunter.memory._HAS_ELEPHANTASM", True):
                result = AnimaManager.ensure_animas(cache_path=anima_cache)

        assert result["hermes-overseer"] == "cached-o"
        assert result["hermes-hunter"] == "new-hunter-id"
        mock_client.create_anima.assert_called_once()

    def test_ensure_animas_handles_create_failure(self, anima_cache):
        """If create_anima fails, we log and continue."""
        from hunter.memory import AnimaManager

        mock_client = MagicMock()
        mock_client.create_anima.side_effect = Exception("409 conflict")

        with patch("hunter.memory.Elephantasm", return_value=mock_client):
            with patch("hunter.memory._HAS_ELEPHANTASM", True):
                result = AnimaManager.ensure_animas(cache_path=anima_cache)

        assert "hermes-overseer" not in result
        assert "hermes-hunter" not in result

    def test_ensure_animas_no_elephantasm(self, anima_cache):
        """If elephantasm isn't installed, returns empty dict."""
        from hunter.memory import AnimaManager

        with patch("hunter.memory._HAS_ELEPHANTASM", False):
            result = AnimaManager.ensure_animas(cache_path=anima_cache)

        assert result == {}

    def test_get_anima_id_found(self, anima_cache):
        """get_anima_id returns the cached ID."""
        from hunter.memory import AnimaManager

        anima_cache.write_text(json.dumps({"hermes-overseer": "abc-123"}))
        assert AnimaManager.get_anima_id("hermes-overseer", cache_path=anima_cache) == "abc-123"

    def test_get_anima_id_missing(self, anima_cache):
        """get_anima_id returns None for unknown names."""
        from hunter.memory import AnimaManager

        anima_cache.write_text(json.dumps({}))
        assert AnimaManager.get_anima_id("nonexistent", cache_path=anima_cache) is None

    def test_get_anima_id_no_cache_file(self, anima_cache):
        """get_anima_id returns None if cache file doesn't exist."""
        from hunter.memory import AnimaManager

        assert AnimaManager.get_anima_id("hermes-overseer", cache_path=anima_cache) is None

    def test_corrupt_cache_handled(self, anima_cache):
        """Corrupt cache file is handled gracefully."""
        from hunter.memory import AnimaManager

        anima_cache.write_text("not valid json {{{")
        result = AnimaManager.get_anima_id("hermes-overseer", cache_path=anima_cache)
        assert result is None


# ---------------------------------------------------------------------------
# OverseerMemoryBridge tests
# ---------------------------------------------------------------------------

class TestOverseerMemoryBridge:

    def test_init_requires_anima_id(self):
        """Raises ValueError if no Anima ID is available."""
        from hunter.memory import OverseerMemoryBridge

        with patch("hunter.memory.AnimaManager.get_anima_id", return_value=None):
            with pytest.raises(ValueError, match="No Anima ID"):
                OverseerMemoryBridge()

    def test_inject_returns_prompt(self, mock_client):
        """inject() returns the memory pack as a prompt string."""
        mock_pack = MagicMock()
        mock_pack.content = "some memories"
        mock_pack.as_prompt.return_value = "## Memory\nPast strategies..."
        mock_client.inject.return_value = mock_pack

        bridge = _make_overseer_bridge(mock_client)
        result = bridge.inject(query="what strategies work?")

        assert result == "## Memory\nPast strategies..."
        mock_client.inject.assert_called_once_with(query="what strategies work?")

    def test_inject_returns_none_when_empty(self, mock_client):
        """inject() returns None when MemoryPack is None."""
        mock_client.inject.return_value = None

        bridge = _make_overseer_bridge(mock_client)
        assert bridge.inject() is None

    def test_inject_returns_none_on_error(self, mock_client):
        """inject() returns None on SDK error (non-fatal)."""
        mock_client.inject.side_effect = Exception("API down")

        bridge = _make_overseer_bridge(mock_client)
        assert bridge.inject() is None

    def test_inject_returns_none_when_no_content(self, mock_client):
        """inject() returns None when MemoryPack has empty content."""
        mock_pack = MagicMock()
        mock_pack.content = ""
        mock_client.inject.return_value = mock_pack

        bridge = _make_overseer_bridge(mock_client)
        assert bridge.inject() is None

    def test_extract_decision(self, mock_client):
        """extract_decision() sends a SYSTEM event with meta."""
        bridge = _make_overseer_bridge(mock_client)

        bridge.extract_decision(
            "Switched Hunter to 72B model",
            meta={"model": "qwen/qwen3.5-72b"},
        )

        mock_client.extract.assert_called_once()
        _, kwargs = mock_client.extract.call_args
        assert kwargs["content"] == "Switched Hunter to 72B model"
        assert kwargs["meta"]["model"] == "qwen/qwen3.5-72b"

    def test_extract_observation(self, mock_client):
        """extract_observation() includes type=observation in meta."""
        bridge = _make_overseer_bridge(mock_client)

        bridge.extract_observation("Hunter stuck in recon loop")

        _, kwargs = mock_client.extract.call_args
        assert kwargs["meta"]["type"] == "observation"
        assert kwargs["content"] == "Hunter stuck in recon loop"

    def test_extract_intervention_result_improvement(self, mock_client):
        """extract_intervention_result() records verdict with high importance for non-neutral."""
        bridge = _make_overseer_bridge(mock_client)

        bridge.extract_intervention_result(
            intervention_id="int_047",
            verdict="improvement",
            metrics_before={"vulns_per_target": 1.2},
            metrics_after={"vulns_per_target": 1.8},
        )

        _, kwargs = mock_client.extract.call_args
        assert kwargs["importance_score"] == 0.9
        assert kwargs["meta"]["verdict"] == "improvement"
        assert kwargs["meta"]["intervention_id"] == "int_047"
        assert kwargs["meta"]["metrics_before"] == {"vulns_per_target": 1.2}
        assert kwargs["meta"]["metrics_after"] == {"vulns_per_target": 1.8}

    def test_extract_intervention_result_neutral(self, mock_client):
        """Neutral verdict gets lower importance score."""
        bridge = _make_overseer_bridge(mock_client)

        bridge.extract_intervention_result(
            intervention_id="int_048",
            verdict="neutral",
            metrics_before={},
            metrics_after={},
        )

        _, kwargs = mock_client.extract.call_args
        assert kwargs["importance_score"] == 0.5

    def test_extract_is_non_fatal(self, mock_client):
        """Extract failures are swallowed — no exception propagated."""
        mock_client.extract.side_effect = Exception("network error")
        bridge = _make_overseer_bridge(mock_client)

        # Should not raise
        bridge.extract_decision("test")
        bridge.extract_observation("test")
        bridge.extract_intervention_result("id", "neutral", {}, {})

    def test_close(self, mock_client):
        """close() calls client.close()."""
        bridge = _make_overseer_bridge(mock_client)
        mock_client.close.reset_mock()  # clear the close from Elephantasm() constructor mock
        bridge.close()
        mock_client.close.assert_called_once()

    def test_session_id_format(self, mock_client):
        """Session ID follows expected format."""
        bridge = _make_overseer_bridge(mock_client)
        assert bridge._session_id.startswith("overseer-")
        # Should be like "overseer-20260311-143025"
        parts = bridge._session_id.split("-")
        assert len(parts) >= 2


# ---------------------------------------------------------------------------
# HunterMemoryBridge tests
# ---------------------------------------------------------------------------

class TestHunterMemoryBridge:

    def test_init_requires_anima_id(self):
        """Raises ValueError if no Anima ID is available."""
        from hunter.memory import HunterMemoryBridge

        with patch("hunter.memory.AnimaManager.get_anima_id", return_value=None):
            with pytest.raises(ValueError, match="No Anima ID"):
                HunterMemoryBridge()

    def test_set_session(self, mock_client):
        """set_session() binds the bridge to a session ID."""
        bridge = _make_hunter_bridge(mock_client)
        bridge.set_session("hunt-002")
        assert bridge._session_id == "hunt-002"

    def test_inject_returns_prompt(self, mock_client):
        """inject() returns memory context as prompt string."""
        mock_pack = MagicMock()
        mock_pack.content = "relevant patterns"
        mock_pack.as_prompt.return_value = "## Memory\nIDOR patterns..."
        mock_client.inject.return_value = mock_pack

        bridge = _make_hunter_bridge(mock_client)
        result = bridge.inject(query="IDOR in REST APIs")
        assert result == "## Memory\nIDOR patterns..."

    def test_inject_returns_none_when_empty(self, mock_client):
        """inject() returns None when no memory available."""
        mock_client.inject.return_value = None

        bridge = _make_hunter_bridge(mock_client)
        assert bridge.inject() is None

    def test_extract_step_tool_call(self, mock_client):
        """extract_step() captures tool calls."""
        bridge = _make_hunter_bridge(mock_client)

        bridge.extract_step({
            "tool_call": {"name": "target_scan", "args": {"target": "acme-api"}},
            "meta": {"duration_s": 12.5},
        })

        mock_client.extract.assert_called_once()
        _, kwargs = mock_client.extract.call_args
        assert "target_scan" in kwargs["content"]
        assert kwargs["session_id"] == "hunt-001"
        assert kwargs["meta"]["duration_s"] == 12.5

    def test_extract_step_assistant_message(self, mock_client):
        """extract_step() captures assistant messages."""
        bridge = _make_hunter_bridge(mock_client)

        bridge.extract_step({
            "assistant_message": "Found potential IDOR vulnerability",
        })

        mock_client.extract.assert_called_once()
        _, kwargs = mock_client.extract.call_args
        assert kwargs["role"] == "assistant"
        assert kwargs["content"] == "Found potential IDOR vulnerability"

    def test_extract_step_both_tool_and_message(self, mock_client):
        """extract_step() captures both tool call and message in one step."""
        bridge = _make_hunter_bridge(mock_client)

        bridge.extract_step({
            "tool_call": {"name": "vuln_assess", "args": {}},
            "assistant_message": "Assessing vulnerability...",
        })

        assert mock_client.extract.call_count == 2

    def test_extract_step_truncates_long_messages(self, mock_client):
        """Assistant messages are truncated to 2000 chars."""
        bridge = _make_hunter_bridge(mock_client)

        long_msg = "x" * 5000
        bridge.extract_step({"assistant_message": long_msg})

        _, kwargs = mock_client.extract.call_args
        assert len(kwargs["content"]) == 2000

    def test_extract_finding(self, mock_client):
        """extract_finding() records a vulnerability with severity-based importance."""
        bridge = _make_hunter_bridge(mock_client)

        bridge.extract_finding({
            "title": "IDOR in /api/v2/users/{id}",
            "severity": "high",
            "cwe": "CWE-639",
            "target": "acme-api",
        })

        _, kwargs = mock_client.extract.call_args
        assert kwargs["importance_score"] == 0.9
        assert kwargs["meta"]["cwe"] == "CWE-639"
        assert kwargs["meta"]["type"] == "finding"
        assert "IDOR" in kwargs["content"]

    def test_extract_finding_critical_importance(self, mock_client):
        """Critical severity maps to importance 1.0."""
        bridge = _make_hunter_bridge(mock_client)

        bridge.extract_finding({
            "title": "RCE via deserialization",
            "severity": "critical",
        })

        _, kwargs = mock_client.extract.call_args
        assert kwargs["importance_score"] == 1.0

    def test_extract_result(self, mock_client):
        """extract_result() records session summary with only scalar meta."""
        bridge = _make_hunter_bridge(mock_client)

        bridge.extract_result({
            "findings_count": 3,
            "target": "acme-api",
            "duration_s": 120.5,
            "complex_field": {"nested": "data"},  # should be filtered out
        })

        _, kwargs = mock_client.extract.call_args
        assert kwargs["meta"]["findings_count"] == 3
        assert kwargs["meta"]["type"] == "session_result"
        assert "complex_field" not in kwargs["meta"]
        assert "Session complete. Findings: 3" in kwargs["content"]

    def test_check_duplicate_found(self, mock_client):
        """check_duplicate() returns summary when similarity > 0.85."""
        mock_pack = MagicMock()
        mock_memory = MagicMock()
        mock_memory.similarity = 0.92
        mock_memory.summary = "IDOR in user endpoint found in acme-api on 2026-03-01"
        mock_pack.long_term_memories = [mock_memory]
        mock_client.inject.return_value = mock_pack

        bridge = _make_hunter_bridge(mock_client)
        result = bridge.check_duplicate("IDOR vulnerability in /api/users endpoint")

        assert result == "IDOR in user endpoint found in acme-api on 2026-03-01"

    def test_check_duplicate_not_found(self, mock_client):
        """check_duplicate() returns None when similarity is too low."""
        mock_pack = MagicMock()
        mock_memory = MagicMock()
        mock_memory.similarity = 0.60
        mock_memory.summary = "unrelated finding"
        mock_pack.long_term_memories = [mock_memory]
        mock_client.inject.return_value = mock_pack

        bridge = _make_hunter_bridge(mock_client)
        assert bridge.check_duplicate("something new") is None

    def test_check_duplicate_no_memories(self, mock_client):
        """check_duplicate() returns None when no memories exist."""
        mock_pack = MagicMock()
        mock_pack.long_term_memories = []
        mock_client.inject.return_value = mock_pack

        bridge = _make_hunter_bridge(mock_client)
        assert bridge.check_duplicate("anything") is None

    def test_check_duplicate_null_similarity(self, mock_client):
        """check_duplicate() returns None when similarity is None."""
        mock_pack = MagicMock()
        mock_memory = MagicMock()
        mock_memory.similarity = None
        mock_memory.summary = "some finding"
        mock_pack.long_term_memories = [mock_memory]
        mock_client.inject.return_value = mock_pack

        bridge = _make_hunter_bridge(mock_client)
        assert bridge.check_duplicate("anything") is None

    def test_check_duplicate_api_error(self, mock_client):
        """check_duplicate() returns None on API error (non-fatal)."""
        mock_client.inject.side_effect = Exception("API down")

        bridge = _make_hunter_bridge(mock_client)
        assert bridge.check_duplicate("anything") is None

    def test_extract_is_non_fatal(self, mock_client):
        """All extract calls are non-fatal."""
        mock_client.extract.side_effect = Exception("network error")
        bridge = _make_hunter_bridge(mock_client)

        # None of these should raise
        bridge.extract_step({"tool_call": {"name": "test", "args": {}}})
        bridge.extract_step({"assistant_message": "hello"})
        bridge.extract_finding({"title": "test", "severity": "low"})
        bridge.extract_result({"findings_count": 0})

    def test_close(self, mock_client):
        """close() calls client.close()."""
        bridge = _make_hunter_bridge(mock_client)
        mock_client.close.reset_mock()
        bridge.close()
        mock_client.close.assert_called_once()


# ---------------------------------------------------------------------------
# _severity_to_importance tests
# ---------------------------------------------------------------------------

class TestSeverityToImportance:

    def test_all_levels(self):
        from hunter.memory import _severity_to_importance

        assert _severity_to_importance("critical") == 1.0
        assert _severity_to_importance("high") == 0.9
        assert _severity_to_importance("medium") == 0.7
        assert _severity_to_importance("low") == 0.5
        assert _severity_to_importance("info") == 0.3

    def test_case_insensitive(self):
        from hunter.memory import _severity_to_importance

        assert _severity_to_importance("HIGH") == 0.9
        assert _severity_to_importance("Critical") == 1.0

    def test_unknown_defaults_to_medium(self):
        from hunter.memory import _severity_to_importance

        assert _severity_to_importance("unknown") == 0.5
