# Ormas Client

The public local runner and MCP client for Ormas coding dispatch.

## Beta installation

Requires [`uv`](https://docs.astral.sh/uv/). The installer creates an isolated Python 3.12 tool
environment and pins the OpenHands runtime used by the certified Ormas fleet.

```bash
uv tool install --python 3.12 'ormas-client[openhands] @ git+https://github.com/heroncovelabs/ormas-client.git'
ormas doctor
```

Authenticate without putting credentials in a source file:

```bash
ormas runner login --access-key '<tb_live key>' --openrouter-key '<sk-or-v1 key>'
ormas doctor
ormas repo add trading-system "$PWD"
```

The credentials are stored in the operating-system keyring. Never paste them into an issue,
repository, task brief, or chat transcript.

Task execution is dry-run by default:

```bash
ormas runner start --repo-alias trading-system --brief 'describe the proposed small change'
```

The paid beta is bounded. Use `--no-dry-run` only during an attended rehearsal after `ormas
doctor` is fully green.

