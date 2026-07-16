"""Local OpenHands executor for the certified OHxOpenRouter tuples.

This module drives the installed ``openhands`` SDK contract
(``from openhands.sdk import LLM, Agent, Conversation, AgentContext`` and
``from openhands.tools.preset.default import get_default_tools``)
programmatically against a disposable, detached git worktree. Only the local
OpenRouter key and the public OpenRouter base URL
(``https://openrouter.ai/api/v1``) are ever handed to the model; the
customer's local repository path and secrets never leave the machine.
"""

from __future__ import annotations

import math
from pathlib import Path


def _finite_observed_cost(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _conversation_observed_cost(conversation: object) -> float | None:
    try:
        stats = getattr(conversation, "conversation_stats")
        metrics = stats.get_combined_metrics()
        return _finite_observed_cost(getattr(metrics, "accumulated_cost"))
    except (AttributeError, TypeError):
        return None

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# The only certified tuples that are permitted to execute locally. Anything
# outside this allow-list must fail closed.
CERTIFIED_TUPLES: dict[str, str] = {
    "glm52-openrouter-oh": "z-ai/glm-5.2",
    "grok45-openrouter-oh": "x-ai/grok-4.5",
    "gpt56-luna-openrouter-oh": "openai/gpt-5.6",
    "deepseek-v4-flash-openrouter-oh": "deepseek/deepseek-v4-flash",
    "deepseek-v4-pro-openrouter-oh": "deepseek/deepseek-v4-pro",
}


def resolve_model(tuple_id: str) -> str:
    """Map a certified tuple id to its OpenRouter model id or fail closed."""
    try:
        return CERTIFIED_TUPLES[tuple_id]
    except KeyError as exc:
        raise ValueError(
            f"tuple '{tuple_id}' is not a certified OHxOpenRouter tuple; refusing to execute"
        ) from exc


# Bound the agent to a reasonable number of turns so a runaway model cannot
# spin forever against the disposable worktree.
MAX_ITERATIONS_PER_RUN = 100


def _agents_md(worktree: Path) -> str | None:
    """Read AGENTS.md as agent context when present in the worktree."""
    agents = worktree / "AGENTS.md"
    if agents.is_file():
        return agents.read_text(encoding="utf-8")
    return None


def run_openhands(
    *,
    tuple_id: str,
    openrouter_key: str,
    worktree: Path,
    brief: str,
    budget_usd: float,
) -> dict[str, object]:
    """Run OpenHands and return its transcript plus observed SDK cost.

    OpenHands is driven with its default toolset and the browser disabled. The
    model only ever sees the disposable worktree, never the registered checkout.
    The local OpenRouter key is handed directly to the SDK's ``LLM`` and never
    crosses the control-plane or gateway HTTP boundary. ``budget_usd`` remains a
    caller compatibility parameter; it is not an observed-cost measurement.
    """
    model = resolve_model(tuple_id)

    # Imported lazily so the CLI, tests and dry-run path do not require the
    # heavy optional ``openhands`` dependency to be installed.
    from openhands.sdk import LLM, Agent, AgentContext, Conversation
    from openhands.tools.preset.default import get_default_tools

    # These provider filters are mandatory; fail when no eligible endpoint exists.
    llm = LLM(
        service_id="ormas-client",
        model=f"openrouter/{model}",
        api_key=openrouter_key,
        base_url=OPENROUTER_BASE_URL,
        litellm_extra_body={
            "provider": {"zdr": True, "data_collection": "deny"},
        },
        caching_prompt=False,
        prompt_cache_retention=None,
    )

    # Browser disabled for the certified local runner.
    tools = get_default_tools(enable_browser=False)

    agents_md = _agents_md(worktree)
    agent_context = (
        AgentContext(system_message_suffix=agents_md) if agents_md is not None else None
    )
    agent = Agent(llm=llm, tools=tools, agent_context=agent_context)

    conversation = Conversation(
        agent=agent,
        workspace=str(worktree),
        max_iteration_per_run=min(MAX_ITERATIONS_PER_RUN, 500),
    )
    conversation.send_message(brief)
    conversation.run()
    result: dict[str, object] = {
        "transcript": str(getattr(conversation, "get_state", lambda: "")() or ""),
    }
    observed_cost = _conversation_observed_cost(conversation)
    if observed_cost is not None:
        result["observed_cost_usd"] = observed_cost
    return result
