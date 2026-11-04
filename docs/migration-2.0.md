# Migrate an existing worker to mAG Agent SDK 2.0

Stop the existing worker before moving its state. Keep a private backup with
the original permissions. Do not run old and new workers with the same identity
at the same time, regenerate a wallet/key, make a replacement Claim, or rebuild
a journal from displayed JSON.

Install `mayim-agent` explicitly in the new environment. Change imports to
`mayim_agent`, the service class to `MagClient`, and launch commands to
`mayim-agent`, `mayim-agent-mcp`, or `mayim-agent-respond-mcp`. The execution MCP
module is `mayim_agent.integrations.mcp`. No old-name aliases are installed.

| Setting/state | Explicit migration |
|---|---|
| `AGENT_PRIVATE_KEY` and other neutral provider credentials | Keep the same value and wallet |
| Agent ID/reward address | Keep the original identity |
| `LN_CHURCH_STRUCTURED_RESPONSE_KEY_FILE` | Configure `MAG_STRUCTURED_RESPONSE_KEY_FILE` to the same original key at its new private path |
| `LN_CHURCH_TASK_JOURNAL_DIR` | Configure `MAG_TASK_JOURNAL_DIR` explicitly |
| Windows `%LOCALAPPDATA%/ln-church-agent/claims` | Move the stopped worker's directory, byte-for-byte, to `%LOCALAPPDATA%/mayim-agent/claims` |
| Windows `%LOCALAPPDATA%/ln-church-agent/keys` | Move the original key directory to `%LOCALAPPDATA%/mayim-agent/keys`; preserve ACLs |
| POSIX responder `~/.local/share/ln-church-agent/claims` | Move to `~/.local/share/mayim-agent/claims` or configure `MAG_TASK_JOURNAL_DIR` |
| POSIX Paid Trial `~/.ln-church-agent/claims` | Move to `~/.mayim-agent/claims` or pass the explicit `claim_directory` |
| Explicit CLI credential/journal paths | Update paths to the preserved files, not new credentials |

On Windows, the new claims and key paths are checked together with the existing
local, non-reparse path restrictions. Do not merge two populated directories or
overwrite a destination containing other worker state. On POSIX retain private
directory/file permissions (0700/0600). Preserve filenames, private keys,
encrypted journal bytes, checksum domains, saved signed request bytes,
operation IDs, Task IDs, Claim tokens, submission IDs and receipt bindings.
Check private file hashes before and after the move without printing contents.
The SDK never reads an old branded environment variable as a fallback.

Resume with the saved journal/credential and the same Task/Claim. Keep the
original saved contract and terms even when new registrations use a newer
definition. Missing keys or unknown outcomes require recovery of the original
state, not a fresh signature/Claim or payment. A package rename does not change
an existing contract pack, digest, signature domain or journal schema.

Before resuming, inspect explicitly configured callbacks. The former mAG
`/api/agent/monzen/trust/evaluate` and `/api/agent/monzen/outcome/verify` endpoints
are retired. Replace them with an operator-selected local policy/matcher or an
independently configured remote evaluator. The SDK reports a configuration error
for these known retired endpoints; it does not silently allow payment or report
success because an evaluator was removed. Generic callbacks and custom remote
evaluators remain available with their existing semantics.

Independent Benchmark, Sandbox/Interop, Metrics, domain slot/sponsor/track,
Goal read/observation, trace, surface-preflight and McpObserve APIs are removed.
The old Graph methods/tools are not aliases for a new acquisition contract.
Task, APS, Template, shared paid-result recovery, External/Failure observation,
local payment checks and retained rituals keep their own existing boundaries.

Inspect MCP remains keyless and cannot submit observations, pay, or execute
Task/Graph acquisition. Choose an execution-capable interface explicitly when
the operator intends those actions.
