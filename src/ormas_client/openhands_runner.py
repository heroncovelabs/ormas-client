"""Local OpenHands executor for the certified OHxOpenRouter tuples.

This module runs ``openhands==1.16.0`` programmatically against a disposable,
detached git worktree. Only the local OpenRouter key and the public OpenRouter
base URL (``https://openrouter.ai/api/v1``) are ever handed to the model; the
customer's local repository path and secrets never leave the machine.
"""

from __future__ import annotations

from pathlib import Path

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


def _agent_context(worktree: Path) -> str | None:
    """Include AGENTS.md as agent context when present in the worktree."""
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
) -> str:
    """Run OpenHands over ``worktree`` for ``brief`` and return the transcript.

    OpenHands is driven with its default toolset and the browser disabled. The
    model only ever sees the disposable worktree, never the registered checkout.
    """
    model = resolve_model(tuple_id)

    # Imported lazily so the CLI, tests and dry-run path do not require the
    # heavy optional ``openhands`` dependency to be installed.
    from openhands.controller.agent import Agent  # type: ignore
    from openhands.core.config import LLMConfig  # type: ignore
    from openhands.core.conversation import Conversation  # type: ignore
    from openhands.llm.llm import LLM  # type: ignore

    llm = LLM(
        LLMConfig(
            model=f"openrouter/{model}",
            api_key=openrouter_key,
            base_url=OPENROUTER_BASE_URL,
            max_output_tokens=None,
        )
    )
    agent = Agent.get_cls("CodeActAgent")(llm=llm, config=None)  # default tools
    context = _agent_context(worktree)
    instruction = brief if context is None else f"{brief}\n\n# AGENTS.md\n{context}"

    conversation = Conversation(
        agent=agent,
        workspace=str(worktree),
        max_budget_per_task=budget_usd,
        # Browser tool disabled for the certified local runner.
        enable_browser=False,
    )
    conversation.send_message(instruction)
    conversation.run()
    return str(getattr(conversation, "get_state", lambda: "")() or "")
