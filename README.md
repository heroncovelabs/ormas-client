# Ormas Client

The public local runner and MCP client for Ormas coding dispatch.

The client seam is intentionally inspectable: local execution, credential handling, MCP adapters,
and verification belong on the customer side of the trust boundary. Routing intelligence,
reputation evidence, economics, and settlement remain service-side. See
[`docs/OPEN_SOURCE_BOUNDARY.md`](docs/OPEN_SOURCE_BOUNDARY.md) for the product boundary, current
MIT status, proposed Apache 2.0 transition, trademark follow-up, and signed-receipt requirements.

## Beta installation

Requires [`uv`](https://docs.astral.sh/uv/). The installer creates an isolated Python 3.12 tool
environment and pins the OpenHands runtime used by the certified Ormas fleet.

```bash
uv tool install --python 3.12 'ormas-client[openhands] @ git+https://github.com/heroncovelabs/ormas-client.git'
ormas doctor
```

Supported platforms are macOS, Linux and WSL2.

Authenticate without putting credentials in a source file:

```bash
ormas runner login --access-key '<tb_live key>' --openrouter-key '<sk-or-v1 key>'
ormas doctor
ormas repo add trading-system "$PWD"
```

Both keys are stored only in the operating-system keyring. The config file keeps IDs, URLs and
local repo aliases but never a key. `login` separates the control plane (`--control-url`, default
`https://ormas.ai`) from the gateway (`--gateway-url`, default `https://ormas-gateway.fly.dev`).
Never paste credentials into an issue, repository, task brief, or chat transcript.

Task execution is dry-run by default. The runner registers a healthy capacity-1 runner and the
repository at git HEAD, creates a sanitized task and claims a lease without touching your checkout:

```bash
ormas runner start --repo-alias trading-system \
  --brief 'describe the proposed small change' \
  --verify-command 'pytest -q' \
  --allowed-path 'src/**' --allowed-path 'tests/**'
```

`--allowed-path` is repeatable and gates the produced diff. Your local path and OpenRouter key
never cross either HTTP boundary.

The first live run uses `--no-dry-run`. It fetches a routing preview from the gateway (with no
repo path, source or key), executes only certified OpenHands×OpenRouter tuples in a disposable
detached git worktree, enforces the allowed-path and verify gates before committing, and never
auto-merges. Use it only during an attended rehearsal after `ormas doctor` is fully green.
