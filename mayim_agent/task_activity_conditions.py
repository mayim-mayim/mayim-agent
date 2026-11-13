"""Pre-registration and pre-Claim guidance for the fixed Activity Map policy."""

ACTIVITY_MAP_NOTICE = (
    "For Scheduled 1.2 and Discovery 1.1, accepted public-safe reports may be linked "
    "across Tasks by the saved reward network and wallet, with Task/result references, "
    "activity type, original acceptance time, permitted result state and source. "
    "Scheduled publishes the canonical origin verified at registration with the Task "
    "and reporting wallet. Discovery uses actual bound public-safe observed origins; "
    "the requester does not select targets. Acceptance does not certify visits, "
    "ownership, safety or payment. Viewing and paid retrieval cover at most 30 JST "
    "calendar days; minimum permitted history is retained privately while the "
    "activity-history service continues. Normal raw-data expiry differs from explicit "
    "publication stop/deletion, which withdraws retained and derived output. Private "
    "Manifest information, endpoint paths/queries, credentials, private execution IDs "
    "and raw reports are excluded. Existing operations retain their original conditions."
)


ANSWER_REVIEW_POLICY_ID = 'mag.answer-reuse-review.public.v1'
ANSWER_REVIEW_NOTICE = (
    'Each Task has a review budget of 10% of its registration fee. APS base fees are excluded. '
    'New Claims can pause and resume, or close with unfilled slots when actual review costs reach '
    'the budget. Accepted Claims keep their original deadlines and reward rights. Responses '
    'excluded for suspected reuse receive no reward and do not contribute to the aggregates.'
)


def validate_reward_eligibility(value, final, amount):
    expected = ('ELIGIBLE' if int(amount) > 0 else 'INELIGIBLE') if final else 'PENDING'
    if (value['reward_eligibility'] != expected
            or value['reward_exclusion_reason'] not in (None, 'SUSPECTED_REUSE')
            or value['reward_exclusion_reason'] is not None and expected != 'INELIGIBLE'):
        raise ValueError('Invalid public reward eligibility.')
