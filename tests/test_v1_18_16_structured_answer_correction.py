"""Pack-independent SDK checks against fixed answer specification c1 §§3-7.

Authority: Charter 045b5d4d09222c5efab5bfbedce9b13e06a80455, instructed by
eab63a63e9fd602a2373a9326a5b0dc107612c11. Controlled exchanges below are SDK
observations, not a corrected Backend/public-correction or final-wheel test.
"""
import copy
import hashlib
import json
import os

import pytest

from mayim_agent import structured_response_contract as c
from mayim_agent.structured_response_client import StructuredResponseClient
from mayim_agent.structured_response_journal import StructuredResponseJournal
from mayim_agent.structured_response_models import (
    FrozenStructuredResponseReport, StructuredResponseTask,
)
from mayim_agent.structured_response_transport import (
    StructuredResponseError, StructuredResponseTransport,
)
from mayim_agent.task_journal import JournalError
from test_v1_18_14_structured_response_prepack import (
    ADDRESS, ASSUMPTIONS, TASK, Venue, answer, claim, error, raw, receipt, status, task,
)

VALUES = {
    'a1_1': {'candidates': [], 'method': ''},
    'a1_2': {'known_before_study': False},
    'a1_3': {'candidates': [], 'method': ''},
    'a1_4': {'identified': False, 'url': None, 'method': ''},
    'category': {'name': ''},
    'competitors': {'candidates': [{'name': '', 'url': None}]},
}


@pytest.fixture(autouse=True)
def _answer_correction_key(tmp_path_factory, monkeypatch):
    directory = tmp_path_factory.mktemp('answer-correction-keys')
    directory.chmod(0o700)
    path = directory / 'test-only-journal.key'
    path.write_bytes(os.urandom(32)); path.chmod(0o600)
    monkeypatch.setenv('MAG_STRUCTURED_RESPONSE_KEY_FILE', str(path))


def aps_task(keys, *, kind=None):
    pack = c.load_contract_pack()
    bank = json.loads(pack['resources']['question-bank.json'])
    kind = kind or ('BLIND' if keys[0] in {'a1_1', 'a1_3'} else 'DISCLOSED')
    definitions = [copy.deepcopy(next(i for i in bank['items'] if i['item_id'] == k)) for k in keys]
    if kind == 'BLIND':
        for item in definitions:
            item['content_conditions'] = []
    wire = task(definitions)
    wire.update(task_definition_digest=pack['manifest']['task_definition_digest'],
        evaluation_profile_id='aps-blind.v1' if kind == 'BLIND' else 'aps-disclosed.v1',
        evaluation_profile_digest=c.digest(json.loads(pack['resources']['evaluation-profile.json'])),
        question_bank_version=bank['version'], question_bank_digest=c.digest(bank), aps_kind=kind)
    return rebind(wire)


def rebind(wire):
    wire['terms_digest'] = c.digest({k: wire[k] for k in c._TERM_FIELDS if k in wire})
    return wire


def check(key, value=None, *, state='answered', reason=None, wire=None, **extra):
    wire = wire or aps_task([key])
    if value is None and state == 'answered':
        value = copy.deepcopy(VALUES[key])
    if reason is None:
        reason = 'Observed reason.' if key in {'category', 'competitors'} or state != 'answered' else ''
    entry = dict(answer(key, state, value, reason), **extra)
    return c.validate_answer(ASSUMPTIONS, [entry], wire['items'], task=wire)


@pytest.mark.parametrize('key', VALUES)
def test_six_answered_closed_values(key):
    value = copy.deepcopy(VALUES[key])
    assert check(key, value)['items'][0]['value'] == value
    for field in value:
        missing = dict(value); missing.pop(field)
        with pytest.raises(ValueError): check(key, missing)
    with pytest.raises(ValueError): check(key, dict(value, score=1))


@pytest.mark.parametrize('key', VALUES)
@pytest.mark.parametrize('state', ['answered', 'unknown', 'not_performed', 'not_disclosed'])
def test_status_value_reason_and_public_definition(key, state):
    wire = aps_task([key])
    if state not in wire['items'][0]['allowed_statuses']:
        with pytest.raises(ValueError): check(key, state=state)
        return
    result = check(key, state=state)
    assert result['items'][0]['status'] == state
    if state != 'answered':
        with pytest.raises(ValueError): check(key, state=state, value='invented')
        with pytest.raises(ValueError): check(key, state=state, reason=' \t\n')


@pytest.mark.parametrize('key', VALUES)
def test_no_self_score_or_evidence_field_on_six_items(key):
    for field in ('evidence_mode', 'score', 'q', 'p'):
        with pytest.raises(ValueError): check(key, **{field: 'executed'})


@pytest.mark.parametrize('key', ['category', 'competitors'])
def test_answered_category_and_competitors_need_reason(key):
    with pytest.raises(ValueError): check(key, reason=' \t\n')


@pytest.mark.parametrize('key', ['a1_1', 'a1_3', 'a1_4'])
def test_empty_method_remains_structure_only_and_is_not_completed(key):
    assert check(key)['items'][0]['value']['method'] == ''
    assert check(key)['items'][0]['reason'] == ''


@pytest.mark.parametrize('key,maximum,minimum', [('a1_1', 5, 0), ('a1_3', 10, 0), ('competitors', 3, 1)])
def test_candidate_count_order_and_duplicates(key, maximum, minimum):
    for count in [0, minimum, maximum, maximum + 1]:
        value = copy.deepcopy(VALUES[key])
        value['candidates'] = [{'name': 'repeated', 'url': None}] * count
        if minimum <= count <= maximum:
            assert check(key, value)['items'][0]['value']['candidates'] == value['candidates']
        else:
            with pytest.raises(ValueError): check(key, value)
    value['candidates'] = [{'name': name, 'url': None} for name in ['Z', 'A', 'Z']]
    assert check(key, value)['items'][0]['value']['candidates'] == value['candidates']


@pytest.mark.parametrize('value', [0, 1, 'true', 'false', None])
@pytest.mark.parametrize('key,field', [('a1_2', 'known_before_study'), ('a1_4', 'identified')])
def test_no_boolean_coercion(key, field, value):
    body = copy.deepcopy(VALUES[key]); body[field] = value
    with pytest.raises(ValueError): check(key, body)


@pytest.mark.parametrize('url', [None, 'https://EXAMPLE.invalid:8443/A#Keep',
    'https://user:pass@example.invalid:444/path', 'HTTPS://example.invalid/'])
def test_report_url_keeps_original_and_does_not_apply_fetch_policy(url):
    assert check('a1_4', {'identified': False, 'url': url, 'method': ''})['items'][0]['value']['url'] == url
    value = {'candidates': [{'name': '', 'url': url}], 'method': ''}
    assert check('a1_1', value)['items'][0]['value'] == value


@pytest.mark.parametrize('url', [True, 1, {}, [], 'http://example.invalid', '/relative', 'https:///missing-host'])
def test_report_url_requires_https_host_string_or_null(url):
    with pytest.raises(ValueError): check('a1_4', {'identified': True, 'url': url, 'method': ''})


@pytest.mark.parametrize('key,field', [('category', 'name'), ('a1_1', 'name'), ('a1_3', 'method'), ('a1_4', 'url')])
def test_report_utf8_4096_byte_boundary(key, field):
    for length in (513, 4096, 4097):
        prefix = 'https://example.invalid/' if field == 'url' else ''
        remaining = length - len(prefix)
        text = prefix + '界' * (remaining // 3) + 'a' * (remaining % 3)
        assert len(text.encode()) == length
        value = copy.deepcopy(VALUES[key])
        if key == 'a1_1': value['candidates'] = [{'name': text, 'url': None}]
        else: value[field] = text
        if length <= 4096:
            assert check(key, value)['items'][0]['value'] == value
        else:
            with pytest.raises(ValueError): check(key, value)


def test_canonical_item_limit_is_not_a_smaller_reason_limit():
    wire = aps_task(['category'])
    entry = answer('category', value={'name': 'synthetic'}, reason='')
    room = 8192 - len(c.canonical_bytes(entry))
    entry['reason'] = 'r' * room
    assert len(c.canonical_bytes(entry)) == 8192
    assert c.validate_answer(ASSUMPTIONS, [entry], wire['items'], task=wire)['items'] == [entry]
    entry['reason'] += 'r'
    with pytest.raises(ValueError): c.validate_answer(ASSUMPTIONS, [entry], wire['items'], task=wire)
    # JSON escaping counts too, not just character count.
    entry['reason'] = '\n' * room
    with pytest.raises(ValueError): c.validate_answer(ASSUMPTIONS, [entry], wire['items'], task=wire)


def test_assumptions_combined_limit_and_no_unicode_or_numeric_repair():
    wire = aps_task(['a1_2']); entry = answer('a1_2', value=VALUES['a1_2'])
    shared = dict(must='界' * 1365, volume='x', outcome='')
    assert c.validate_answer(shared, [entry], wire['items'], task=wire)['shared_assumptions'] == shared
    shared['outcome'] = 'x'
    with pytest.raises(ValueError): c.validate_answer(shared, [entry], wire['items'], task=wire)
    for name in ['\ud800', float('nan'), float('inf')]:
        with pytest.raises(ValueError): check('category', {'name': name})


@pytest.mark.parametrize('kind', ['DISCLOSED', 'REEVALUATION'])
def test_saved_profile_selects_disclosed_items(kind):
    wire = aps_task(['category'], kind=kind)
    assert check('category', wire=wire)['items'][0]['value'] == {'name': ''}


@pytest.mark.parametrize('profile,kind', [
    ('structured-response-custom.v1', 'DISCLOSED'), ('aps-deep.v1', 'DEEP_DIVE'),
    ('agent-profile.v1', 'PROFILE'), ('aps-disclosed.v1', 'BLIND'),
    ('aps-blind.v1', 'DISCLOSED'), ('aps-common-comparison.v1', 'COMMON_COMPARISON'),
])
def test_other_profiles_use_declared_generic_report_fields(profile, kind):
    wire = aps_task(['category'])
    wire.update(evaluation_profile_id=profile, aps_kind=kind)
    wire['items'][0]['report_fields'] = [{'field_id': 'custom', 'type': 'boolean', 'required': True}]
    rebind(wire)
    assert check('category', {'custom': False}, reason='', wire=wire)['items'][0]['value'] == {'custom': False}


def test_same_id_does_not_select_aps_without_saved_task():
    wire = aps_task(['a1_1'])
    with pytest.raises(ValueError, match='Missing report field'):
        c.validate_answer(ASSUMPTIONS, [answer('a1_1', value=VALUES['a1_1'])], wire['items'])
    wire['items'][0]['report_fields'] = [{'field_id': 'custom', 'type': 'string', 'required': True}]
    rebind(wire)
    assert check('a1_1', {'custom': ''}, wire=wire)['items'][0]['value'] == {'custom': ''}


@pytest.mark.parametrize('field,value', [('question', {'en': 'A custom question'}),
    ('required', False), ('allowed_statuses', ['answered', 'unknown', 'not_performed'])])
def test_custom_definition_cannot_borrow_aps_shape(field, value):
    wire = aps_task(['a1_1', 'a1_3']); wire['items'][0][field] = value; rebind(wire)
    entries = [answer(key, value=VALUES[key]) for key in ['a1_1', 'a1_3']]
    with pytest.raises(ValueError): c.validate_answer(ASSUMPTIONS, entries, wire['items'], task=wire)


@pytest.mark.parametrize('field,value', [('terms_digest', 'f' * 64), ('task_definition_digest', 'f' * 64),
    ('question_bank_digest', 'f' * 64), ('evaluation_profile_digest', 'f' * 64),
    ('question_bank_version', 'unknown'), ('task_definition_version', '3.1.0'),
    ('schema_version', 'ln_church.agent_task.structured_response.v3_1')])
def test_broken_or_unfixed_binding_never_falls_back(field, value):
    wire = aps_task(['a1_1']); wire[field] = value
    if field != 'terms_digest': rebind(wire)
    with pytest.raises(ValueError): check('a1_1', wire=wire)


class AnswerVenue(Venue):
    def __call__(self, method, path, query, headers, body, timeout):
        if path.endswith('/claim'):
            self.calls.append((method, path, query, dict(headers), body, timeout))
            payload = claim(self.task); payload['task_definition_digest'] = self.task['task_definition_digest']
            return raw(payload)
        return super().__call__(method, path, query, headers, body, timeout)


def connect(tmp_path, wire=None):
    venue = AnswerVenue(wire or aps_task(['category']))
    client = StructuredResponseClient(transport=StructuredResponseTransport(exchange=venue))
    operation = client.claim(TASK, 'answer-correction', ADDRESS, 'original-claim', journal_directory=tmp_path)
    return client, operation.journal, venue


def legacy_report(journal, *, dispatched, accepted=False):
    """Synthetic pre-correction history: old validator allowed blank category reason."""
    credential = journal.load_claim()
    payload = {k: getattr(credential, k) for k in ('task_id', 'task_type', 'task_definition_version',
        'task_definition_digest', 'terms_digest', 'execution_id')}
    payload.update(schema_version=c.schema('task_completion'), submission_id='sub_' + 'b' * 32,
        context_id=journal.load_task().context_id, shared_assumptions=ASSUMPTIONS,
        items=[answer('category', value={'name': 'Synthetic category'}, reason='')])
    report = FrozenStructuredResponseReport.from_report(payload)
    state = journal._load()
    state.update(report=report.canonical_bytes.decode(), report_sha256=report.report_sha256,
        completion_started=dispatched, completion_unknown=dispatched and not accepted, accepted=accepted,
        last_result={'state': 'accepted', 'receipt': receipt(payload)} if accepted else None)
    journal._write(state)
    return report


@pytest.mark.parametrize('accepted', [False, True])
def test_original_accepted_or_unknown_operation_reopens_and_reads_without_new_validation(tmp_path, accepted):
    client, journal, venue = connect(tmp_path)
    report = legacy_report(journal, dispatched=True, accepted=accepted)
    venue.report = report.report
    with pytest.raises(ValueError): report.validate_binding(journal.load_task(), journal.load_claim())
    reopened = StructuredResponseJournal.open(tmp_path, journal.handle)
    count = len(venue.calls)
    result = client.recover_completion(journal=reopened)
    assert result.state == 'accepted' and result.post_requests == 0
    assert all(call[0] == 'GET' for call in venue.calls[count:])
    assert result.status.report_sha256 == report.report_sha256
    assert reopened.load_report().canonical_bytes == report.canonical_bytes
    # An identical local re-prepare also preserves the original operation.
    assert client.prepare_answer(journal=reopened, shared_assumptions=ASSUMPTIONS,
        items=report.report['items']).canonical_bytes == report.canonical_bytes
    with pytest.raises(JournalError):
        client.prepare_answer(journal=reopened, shared_assumptions=ASSUMPTIONS,
            items=[answer('category', value={'name': 'Changed'}, reason='Changed')], correction=True)


def test_unknown_legacy_operation_replays_only_original_bytes_and_id(tmp_path):
    client, journal, venue = connect(tmp_path)
    report = legacy_report(journal, dispatched=True)
    reopened = StructuredResponseJournal.open(tmp_path, journal.handle)
    before = len(venue.calls)
    result = client.recover_completion(journal=reopened)
    assert result.state == 'accepted' and result.post_requests == 1 and result.status_requests == 1
    calls = venue.calls[before:]
    assert calls[0][1].endswith('/status')
    assert calls[1][4] == report.canonical_bytes
    assert calls[1][3]['Idempotency-Key'] == report.report['submission_id']


def test_never_dispatched_old_invalid_answer_cannot_bypass_new_submission_validation(tmp_path):
    client, journal, venue = connect(tmp_path)
    report = legacy_report(journal, dispatched=False)
    reopened = StructuredResponseJournal.open(tmp_path, journal.handle)
    before = len(venue.calls)
    with pytest.raises(ValueError):
        client.prepare_answer(journal=reopened, shared_assumptions=ASSUMPTIONS, items=report.report['items'])
    with pytest.raises(JournalError): client.submit(journal=reopened)
    assert venue.calls[before:] == []


@pytest.mark.parametrize('field', ['terms_digest', 'execution_id', 'context_id', 'report_sha256'])
def test_saved_recovery_still_rejects_changed_binding_or_hash(tmp_path, field):
    client, journal, venue = connect(tmp_path)
    report = legacy_report(journal, dispatched=True, accepted=True)
    state = journal._load()
    if field == 'report_sha256': state[field] = 'f' * 64
    else:
        payload = report.report
        payload[field] = {'terms_digest': 'f' * 64, 'execution_id': 'exec_' + 'c' * 32,
            'context_id': 'other-context'}[field]
        raw_bytes = c.canonical_bytes(payload)
        state.update(report=raw_bytes.decode(), report_sha256=hashlib.sha256(raw_bytes).hexdigest())
    journal._write(state)
    with pytest.raises(JournalError): StructuredResponseJournal.open(tmp_path, journal.handle)


def test_actual_client_uses_saved_profile_canonical_order_and_generic_dispatch(tmp_path):
    wire = aps_task(['a1_1', 'a1_3'])
    client, journal, venue = connect(tmp_path, wire)
    entries = [answer(key, value=copy.deepcopy(VALUES[key]), reason='') for key in ['a1_3', 'a1_1']]
    before = len(venue.calls)
    report = client.prepare_answer(journal=journal, shared_assumptions=ASSUMPTIONS, items=entries)
    assert len(venue.calls) == before
    assert [item['item_id'] for item in report.report['items']] == ['a1_1', 'a1_3']
    assert client.submit(journal=journal).state == 'accepted'
    assert venue.calls[-1][4] == report.canonical_bytes
    assert client.get_submission_status(journal=journal).entitlement['state'] == 'PENDING'


def test_raw_and_canonical_body_limits_and_strict_json_are_preserved(tmp_path):
    client, journal, venue = connect(tmp_path)
    report = client.prepare_answer(journal=journal, shared_assumptions=ASSUMPTIONS,
        items=[answer('category', value={'name': 'Synthetic'}, reason='Reason')])
    for raw_bytes in [b' ' * 262145 + report.canonical_bytes,
        report.canonical_bytes[:-1] + b',"context_id":"duplicate"}',
        report.canonical_bytes.replace(b'"Synthetic"', b'NaN'),
        report.canonical_bytes.replace(b'"Synthetic"', b'"\\ud800"')]:
        with pytest.raises(ValueError): FrozenStructuredResponseReport.from_bytes(raw_bytes)
    large = report.report; large['items'][0]['reason'] = 'x' * 262144
    with pytest.raises(ValueError): FrozenStructuredResponseReport.from_report(large)
