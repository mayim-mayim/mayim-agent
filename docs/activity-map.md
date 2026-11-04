# Activity Map acquisition

This interface is part of the private 2.0.0 candidate. It uses the fixed
`/api/agent/activity-map/v1` contract. Public availability is a separate release
decision. It downloads a fixed 30-day generation and confirms completion only
after every referenced part is verified and durably saved.

## Download and recover

```python
from mayim_agent import MagClient, ActivityMapClient, ActivityMapError

# Use your existing identity, payment policy and configured signer/provider.
runtime = MagClient(evm_signer=my_existing_signer, policy=my_payment_policy)
graph = runtime.activity_map("/private/mag-graph")
try:
    result = graph.download(scheme="x402", asset="USDC")
except ActivityMapError as error:
    # Public handles only. The payment may already have happened.
    print(error.code, error.recovery)
```

Keep the directory and journal intact. POSIX directories must be owned by the
current user and mode 0700; files are private mode 0600. Windows uses the existing
`%LOCALAPPDATA%/mayim-agent/claims` boundary. Do not copy only the downloaded parts,
discard credentials, recreate the operation, or rerun download to recover a lost
response. Native Windows qualification remains pending for this candidate.

```python
# Resume needs the original journal, not wallet keys or another payment.
graph = ActivityMapClient("/private/mag-graph")
result = graph.resume(original_operation_id)
state = graph.status(original_operation_id)
```

The same methods have `download_async`, `resume_async`, `status_async` and
`replacement_async` variants. `MagClient` exposes corresponding
`download_activity_map`, `resume_activity_map`, `activity_map_status` and
`replace_activity_map` wrappers and their async variants.

Only `state == "COMPLETE"` confirms completion. `PAYMENT_PENDING` means payment
confirmation is unresolved, not that payment failed. On a transport or integrity
error, retain `error.recovery` and the original journal. If recovery confirms that
the server already completed the purchase, it does not fetch the payload again.
If the server still reports `ACQUIRING`, resume verifies the saved manifest,
catalogs and data, fetches only missing or damaged files, and then sends complete.
A prior complete send record alone cannot establish completion. The original
payment identity, generation and deadline remain bound to the same purchase.

The absolute deadline is the first confirmed payment time plus 24 hours,
including the complete notification. Recovery, date rollover and replacement do
not extend it. Retries are finite: initial request plus two retries, one- and
two-second backoff with jitter, and Retry-After for 429. Parts use concurrency
two, a 10-second connect timeout and a 60-second read inactivity timeout. There
is no background polling. A later explicit resume can retry within the deadline.

## Stopped generations

If status returns `REPLACEMENT_REQUIRED`, inspect the supplied candidate and
reason. Accept it explicitly using its exact generation and manifest hash:

```python
candidate = state["replacement"]
result = graph.replacement(
    original_operation_id, decision="accept",
    generation=candidate["generation"],
    manifest_sha256=candidate["manifest_sha256"],
)
# Or explicitly end acquisition without accepting the replacement:
# graph.replacement(original_operation_id, decision="decline")
```

Replacement keeps the original window, cutoff, policy, schema and deadline.
Each generation has a separate output directory; old and new parts are never
combined. No safe candidate, a declined candidate, or an expired deadline is a
finite unsuccessful acquisition, not an empty successful Graph. The acquisition
contract does not add a refund operation. Authentication failures remain errors.

## CLI and MCP

```sh
mayim-agent graph download --directory /private/mag-graph --scheme x402 --asset USDC
mayim-agent graph resume --directory /private/mag-graph --operation-id ORIGINAL_ID
mayim-agent graph status --directory /private/mag-graph --operation-id ORIGINAL_ID
mayim-agent graph replacement --directory /private/mag-graph --operation-id ORIGINAL_ID --decision decline
```

CLI download uses the existing `AGENT_PRIVATE_KEY` or `LN_API_URL` / `LN_API_KEY`
configuration. Do not pass private keys, recovery secrets or payment proofs on
the command line. Resume/status/replacement use the saved journal without a
wallet signer. JSON errors carry a public recovery handle when the initial
journal was successfully saved.

Graph acquisition does not accept Grants or Faucet fallback. Use a normal payment
scheme for a new purchase. Existing Grant journals are retained on disk but are
not replayed or converted into payment proof. Normal paid purchases continue to
resume with their original journal, proof and recovery credentials.

The execution MCP exposes `download_activity_map`, `resume_activity_map`,
`activity_map_status` and `replace_activity_map`. Inspect MCP and Task responder
MCP do not expose these tools. No standalone complete command bypasses byte
verification and durable storage.

## Candidate validation boundary

The implementation has local HTTP tests against the fixed Backend c3
producer, content reader and control/http/payment modules. Its producer-generated
replacement is explicitly accepted by the SDK after a stop during payment.
Storage, activity inputs, payment verification and provider outcomes are
synthetic; this does not qualify live rails or production infrastructure.
Source and package tests are implementation self-checks, not independent
acceptance. Paid Task 3.2 uses its separately fixed Backend c2 pack;
it is distinct from Graph acquisition payment.
