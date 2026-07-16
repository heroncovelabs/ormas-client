from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

from ormas_client.openhands_runner import run_openhands


def test_openhands_enforces_openrouter_zdr_and_disables_requested_prompt_cache(
    tmp_path, monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeLLM:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    class FakeAgent:
        def __init__(self, **_: object) -> None:
            pass

    class FakeAgentContext:
        def __init__(self, **_: object) -> None:
            pass

    class FakeConversation:
        conversation_stats = SimpleNamespace(
            get_combined_metrics=lambda: SimpleNamespace(accumulated_cost=0.0),
        )

        def __init__(self, **_: object) -> None:
            pass

        def send_message(self, _: str) -> None:
            pass

        def run(self) -> None:
            pass

        def get_state(self) -> str:
            return "done"

    sdk = ModuleType("openhands.sdk")
    sdk.LLM = FakeLLM
    sdk.Agent = FakeAgent
    sdk.AgentContext = FakeAgentContext
    sdk.Conversation = FakeConversation
    default_tools = ModuleType("openhands.tools.preset.default")
    default_tools.get_default_tools = lambda **_: []
    monkeypatch.setitem(sys.modules, "openhands.sdk", sdk)
    monkeypatch.setitem(sys.modules, "openhands.tools.preset.default", default_tools)

    run_openhands(
        tuple_id="grok45-openrouter-oh",
        openrouter_key="sk-or-local-only",
        worktree=tmp_path,
        brief="make a safe change",
        budget_usd=0.25,
    )

    assert captured["litellm_extra_body"] == {
        "provider": {"zdr": True, "data_collection": "deny"},
    }
    assert captured["caching_prompt"] is False
    assert captured["prompt_cache_retention"] is None
