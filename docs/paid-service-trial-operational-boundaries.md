# Paid Service Trial: request, payment and result boundaries

## mAG 2.0: explicitly select Paid 3.2

Use `PaidServiceTrialTaskClient(version="v3_2")` for the fixed 3.2.0 Task
schema. Its definition digest is
`8c44c115574b593274869091d418f4a6e4e086e55893450bf6e91a2b0a6242af`.
Task and Claim validation preserve `activity_map_policy_id`,
`activity_map_epoch`, the saved economic conditions and their digest.
Missing or unknown fields and modified snapshots are rejected.

```python
from mayim_agent.paid_service_trial_client import PaidServiceTrialTaskClient
from mayim_agent.paid_service_trial_contract import version_of
from mayim_agent.paid_service_trial_journal import PaidServiceTrialJournal

# Explicitly discover the new version. No automatic Claim or payment.
with PaidServiceTrialTaskClient(version="v3_2", claim_directory=private_directory) as worker:
    page = worker.list_tasks()

# Resume the original operation after restarting; use its saved version.
claim = PaidServiceTrialJournal.load_claim(private_directory, task_id, execution_id)
journal = PaidServiceTrialJournal(private_directory, claim)
report = journal.load_report()
if report is not None:
    with PaidServiceTrialTaskClient(version=version_of(claim),
                                   claim_directory=private_directory) as worker:
        status = worker.recover_completion(claim, report, journal=journal)
```

The existing Paid purchase helper accepts the new Claim and applies the same
local PaymentPolicy. The purchase request and payment checksum domains remain
unchanged. A recovery operation never replaces the purchase, nonce, key or
Claim. Current offers do not change an earlier Claim's reward or conditions.

Requester support retains existing-purchase sample descriptors and signed
registration read recovery through `OfferRegistrationClient`; there is no new
Paid registration payment wrapper. Catalog and APS requester flows remain
outside the respondent SDK. These are separate from Graph acquisition tools.

The package is a private candidate. DC acceptance, independent testing and
publication remain separate steps. Existing client defaults remain unchanged;
select the new version explicitly.

## Existing v3 seven-day listing contracts

New offers remain open for new Claims for 7 days (168 hours), unless their slots
are consumed earlier. Existing Claim deadlines are unchanged. Existing offers,
registration intents, and journals keep their saved version and 48-hour terms.
Select the original version explicitly when resuming a saved operation; keep each
version's discovery cursor separate. Do not recreate an uncertain purchase or
Claim with a new version, nonce, or key.

This update removes the service's 0.01 USDC purchase cap for new-version Paid
Service Trial tasks. Your spending limits and payment permissions remain
unchanged. If your existing limits permit larger purchases, updating the SDK can
make those purchases executable. Set or review your limits for your intended
spending. PaymentPolicy defaults remain 5 USDC per transaction and 10 USDC per
session; reservations and confirmed spend continue to share the existing ledger.
Purchase cost is the full fixed amount; reward if approved is 0.02 USDC. Purchase
cost may exceed the reward. LN Church does not advance or reimburse that cost.

The pre-existing default selects v3 (definition 3.0.0). Old versions remain available
through explicit `version=` selection. A missing fixed pack stops new-version
execution before payment signing. The HTTP request schema inside Paid v3 remains
`ln_church.paid_service_request.v2`.


An unpaid terms check sends a real request to the target using the same method,
URL and canonical body as the paid request. A POST terms check is **not a dry run**.
A provider should put its 402 payment gate before side effects and consider
idempotency. These are design recommendations, not guarantees checked by LN Church
or the SDK and not additional Task registration requirements.

Terms observation, payment authorization, confirmed payment, the seller's result,
Task evaluation, reward entitlement and confirmed reward payout are separate facts.
An ambiguous paid dispatch does not authorize automatically creating another
purchase or signature. Preserve the original operation and use its prescribed
recovery path. Do not add HEAD probes, custom headers or paid retries to the
fixed request contract.

Read the fixed Guides for the actual Task version:

| Version | Worker | Requester |
| --- | --- | --- |
| v1 | [Worker Guide](https://kari.mayim-mayim.com/agent-task-specs/paid_service_trial.v1/1.0.0/SKILL.md) | [Requester Guide](https://kari.mayim-mayim.com/agent-task-specs/paid_service_trial.v1/1.0.0/requester-guide.md) |
| v2 | [Worker Guide](https://kari.mayim-mayim.com/agent-task-specs/paid_service_trial.v2/2.0.0/SKILL.md) | [Requester Guide](https://kari.mayim-mayim.com/agent-task-specs/paid_service_trial.v2/2.0.0/requester-guide.md) |

This supplemental page does not modify those immutable Guides, packs, eligibility
rules, limits or payment/recovery contracts.
