# MCP and tool integrations

Install `mayim-agent[mcp]` and select the authority appropriate to the application.

| Mode | Launch | Tools |
|---|---|---|
| Inspect | `mayim-agent-mcp` | `inspect_paid_surface`, `explain_recommended_action` |
| Task responder | `mayim-agent-respond-mcp` | Explicit Structured Task operations and same-journal recovery |
| Execution | `python -m mayim_agent.integrations.mcp` | Explicit retained ritual and service operations using configured credentials |

Inspect does not initialize a wallet or signer, pay, write a Task journal, or
submit observations. Public-target and redirect checks remain in force.
Responder operations keep Claim, answer preparation, submission and status
separate. Review the selected Task's eligibility and publication conditions
before an explicit Claim; tuple support does not establish personal eligibility.

Execution requires operator-configured credentials such as `AGENT_PRIVATE_KEY`.
It is not a replacement for the keyless inspect server.
The dedicated McpObserve conversion/submission tools, Benchmark, trace, Metrics
and old Graph tools are removed; old names are not redirected to new contracts.

The LangChain integration retains the existing ritual tools and removes the
Benchmark comparison tool. Install `mayim-agent[langchain]` to use it.
See [migration](migration-2.0.md) before starting an existing worker.
