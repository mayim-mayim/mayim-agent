# Scheduled 1.2 and Discovery 1.1

This private SDK c3 consumes Backend c4 `dca269ae5b71fde69c374c0f1adb59b67a1367b4`
and DC's fixed 120 inputs. It does not publish or deploy them.

## Review before registration and Claim

Accepted public-safe reports may be linked across Tasks by the saved reward
network and wallet. Permitted public fields are the activity identity/type,
original acceptance time, result state, source, allowed Task/result references,
reward network/wallet and supported canonical origin. Acceptance does not
certify visits, ownership, safety, evaluation approval or payment.

Scheduled's origin is the canonical origin verified by existing origin-control
and private Manifest validation at registration. It is fixed with the Task and
reporting wallet. Discovery derives site relationships only from actual bound,
public-safe observations. Its requester cannot choose a target site.

Viewing and paid retrieval cover at most **30 JST calendar days**. Minimum
permitted history is retained privately while the activity-history service
continues. Normal raw report expiry is separate from explicit publication stop
or deletion, which withdraws retained history and derived output. Private
Manifest content/identity, paths, queries, credentials, execution identities
and raw reports are excluded from this permission.

The existing CLI Claim steps display this explanation before dispatch; Python
callers must present `task_activity_conditions.ACTIVITY_MAP_NOTICE` before
Claim or payment. Registration quotes expose `activity_map_notice` for review.
There is no extra consent signature or consent ledger.

## Fixed selection and saved conditions

| Family | Client selection | Definition digest | Task / Claim schema |
|---|---|---|---|
| Scheduled | `AgentTaskV2Client(definition_version="1.2.0")` | `2ebfcca188270ef17660944150c3673b5b157e199cedc02e1abcacc300a0cb11` | `v2_2` / `v2_2` |
| Discovery | `AgentTaskClient(definition_version="1.1.0")` for its list | `2fd692c9fe2c0b8ce62597dbe8f604bdd1afb2e19dd91d147571c7d93ce415b2` | `v1_1` / `v1_1` |

Common discovery includes both tuples. Discovery detail and Claim dispatch from
the server's persisted tuple. Its list uses `task_schema_version=ln_church.agent_task.v1_1`.
The CLI selects it with `task list --definition-version 1.1.0`; `task get` and
`task claim` use the saved Task. Scheduled Claim uses
`task claim-v2 ... --definition-version 1.2.0`; `run-scheduled-http-get-batch`
loads the original definition from its private credential and journal.

`activity_map_policy_id`, `activity_map_epoch` and `terms_digest` stay in new
credentials. Scheduled also retains its canonical origin and complete economic
snapshot. These fields participate in new credential/checkpoint/journal local
equality bindings. They do not turn a local file into server authority. Old
credential/journal bytes and old Task versions retain their original meaning.
Nested snapshots cannot be altered by mutating a returned dictionary.

Scheduled receipt/status intentionally retain `v2_1` envelopes with the `1.2.0`
tuple. Discovery's unchanged Completion/observation/status messages retain their
original schemas; new-pack closed validation still applies. Never infer an
envelope suffix from the definition's version alone.

## Explicit registration and original-operation recovery

Discovery uses `OfferRegistrationClient.prepare_registration(request)`, review
of `quote.payment_terms` and `quote.activity_map_notice`, explicit `quote.sign`,
then `submit_registration`. Its new request has exactly seven fields:

```python
request = {
    "schema_version": "ln_church.task_offer_create_request.payment_surface_discovery.v1_1",
    "task_type": "payment_surface_discovery.v1",
    "task_definition_version": "1.1.0",
    "task_definition_digest": "2fd692c9fe2c0b8ce62597dbe8f604bdd1afb2e19dd91d147571c7d93ce415b2",
    "plan_id": "C50",
    "activity_map_policy_id": "mag.activity-map.public.v1",
    "activity_map_epoch": current_epoch,
}
```

Use the currently advertised epoch, not a date inferred from DB cleanup or
Graph publication. After an uncertain paid send, preserve the original request
and authorization bytes. `restore_registration(original_request, original_header)`
reuses them. `get_registration_challenge` / `read_registration` use a separate,
explicit owner read proof. Discovery operation references are the original
64-hex authorization identity; other families retain their original identities.
Do not create another authorization merely to recover an existing operation.

Scheduled uses `get_economic_offers("scheduled")` followed by
`prepare_scheduled_registration(metadata, manifest, control_key)`. Metadata
contains the registration intent, Task type, definition `1.2.0`, scheduled time,
plan, complete economic terms and policy/epoch. The adapter preserves exact
multipart bytes and the existing private origin-control key. It exposes
`quote.saved_conditions`, including the owner-frozen registration terms digest
and canonical origin derived from the validated Manifest. Review these before
`quote.sign`; use `submit_scheduled_registration` to send that same operation.
There is no `activity_map_origin` request parameter.

After a lost response or restart, use
`read_scheduled_registration(original_intent_id, control_key)`. It is an
owner-authenticated read without current-offer lookup or payment signing. A
retained in-memory operation can explicitly replay its original bytes. Neither
requester adapter creates a new durable payment store; callers retain private
recovery material using their existing secure storage.

C-unset new registrations fail with 503; stale epoch or old new-sale conditions
fail with 409 before charge. Existing saved operations recover under their
original conditions, even after C or current offers change. Existing Task
Claim, late reports and payment recovery do not become new-policy activities.

## Graph and execution boundaries

The same existing Graph Python, CLI and execution MCP acquisition paths consume
the new source projections, verify parts, and handle explicit replacement with
no new fee. Inspect and responder MCP permissions are unchanged; no Scheduled
or Discovery execution tool is added to a surface that did not already have it.
Valid zero, `not_confirmed` coverage and retrieval failure remain distinct.
A locally completed purchase is historical state, not proof of current public
visibility. SDK c3 does not expand public raw-data fields or backfill old Tasks.

Independent Test, exact integration acceptance, freeze and release remain DC
steps after this candidate. Live providers, all rails/Layer composition, real
payments, native Windows/ACL, production cache/routing, performance and cost
are not established by isolated self-checks.
