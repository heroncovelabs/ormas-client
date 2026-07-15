from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .cli import run_registered_task

mcp = FastMCP("ormas")


@mcp.tool()
def ormas_submit_task(
    task: str,
    repo_alias: str,
    verify_command: str,
    allowed_paths: list[str],
    cost_cap_usd: float = 0.25,
    dry_run: bool = True,
) -> dict:
    # Delegates to the SAME runner-v1 service the CLI's `ormas runner start`
    # uses, so the lifecycle (registration, dry-run nonqueueing, task
    # create/claim, nonexecuting gateway preview, certified tuple allowlist,
    # local detached-worktree execution) is defined in exactly one place.
    return run_registered_task(
        repo_alias=repo_alias,
        brief=task,
        verify_command=verify_command,
        allowed_paths=allowed_paths,
        cost_cap_usd=cost_cap_usd,
        dry_run=dry_run,
    )


def main() -> None:
    mcp.run(transport="stdio")

