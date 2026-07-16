# Ormas open-source product boundary

Status: product direction for beta; licensing and trademark changes remain subject to explicit owner/legal approval.

## Why the client seam should be open

The local client is the trust boundary customers install on machines that hold source code and provider credentials. Its credential handling, worktree isolation, allowed-path checks, verification, MCP adapters, and wire protocol should be inspectable and portable.

Open client code is also the lowest-friction way to support Codex, Claude Code, OpenHands, other MCP hosts, macOS, Linux, and WSL2. A customer can already call a provider directly; hiding model identifiers or local execution code is not the Ormas moat.

## Public seam

The intended public surface includes:

- the CLI, SDK, MCP server, and host adapters;
- the local OpenHands runner and disposable-worktree safety controls;
- runner-v1 schemas and protocol documentation;
- local credential storage, path enforcement, and verification execution;
- transparent tuple/model identity in results; and
- integration examples and compatibility tests.

The local certified-tuple list is an execution allowlist. It answers whether a server-selected tuple may execute safely; it must not become a copy of the routing policy.

## Ormas service intelligence

The hosted service retains the changing intelligence and operational evidence that create customer value:

- task classification and routing policy;
- model × harness × archetype reputation and capability-boundary data;
- cost, latency, reserve, escalation, and miner-market policy;
- verification-quality calibration and learning loops;
- outcome history, abuse controls, billing, and settlement; and
- signing keys and service-side receipt validation.

The value proposition is not secret model names. It is selecting the cheapest model likely to pass a particular task, proving the result, escalating when required, and improving those decisions from accumulated outcomes.

## Licensing and trademark

This repository is currently licensed under the MIT License. The proposed durable policy is Apache License 2.0 for the client seam because it adds an explicit patent grant while remaining permissive. Changing the license requires an explicit relicensing decision and contributor-provenance review; this document does not itself change the license.

Code licensing does not grant rights to impersonate the Ormas service or use Ormas names and marks in a way that implies endorsement. A separate public trademark policy must define nominative compatibility language, fork naming, logo use, and prohibited service impersonation before launch promotion relies on that boundary.

## Signed trust boundary

An altered client must not be able to fabricate billable success. The production design should use versioned, signed routing and verification receipts binding at least the tenant, task, base commit, selected tuple, verification command/result, cost, and settlement identity. Secrets, plaintext lease tokens, source, and absolute local paths must never appear in receipts.

## Post-launch moat review

After beta, Ormas should run a recurring forkability and replication review. It should measure routing lift, verification precision, reputation coverage, cost advantage, outcome volume, and customer time-to-trust. If the service cannot demonstrate value beyond what a fork can obtain from the public client plus direct provider access, the moat thesis has failed and the roadmap must change.
