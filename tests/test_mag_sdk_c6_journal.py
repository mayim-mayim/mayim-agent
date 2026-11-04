"""T-HG-C1-SDKC5-01: exact independent 402 and private persistence boundary."""
import asyncio
import hashlib
import json
import math
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from mayim_agent.activity_map import ActivityMapClient, ActivityMapError
from mayim_agent.activity_map_journal import journal_bytes
from mayim_agent.immediate_visit_journal import _read, _write
from mayim_agent.immediate_visit_profile import jcs_bytes
from mayim_agent.task_journal import JournalError, JournalPersistenceError

FIXTURE = Path(__file__).parent / 'fixtures/mag/c6'


@pytest.mark.parametrize('asynchronous', [False, True])
def test_exact_independent_402_saved_and_reloaded(tmp_path, asynchronous):
    raw = (FIXTURE / 'real-402.json').read_bytes()
    provenance = json.loads((FIXTURE / 'provenance.json').read_text())
    assert hashlib.sha256(raw).hexdigest() == provenance['response_sha256']
    old = json.loads((FIXTURE / 'initial-journal.json').read_text())['payload']
    calls = []
    def exchange(request):
        calls.append(request.url.path)
        if request.method == 'GET':
            return httpx.Response(200, content=(FIXTURE / 'manifest.json').read_bytes())
        assert request.content.decode() == old['request_bytes']
        return httpx.Response(402, content=raw)
    worker = ActivityMapClient(tmp_path, transport=httpx.MockTransport(exchange))
    with patch('mayim_agent.activity_map.secrets.token_hex', side_effect=[old['operation_id'], old['recovery_secret']]):
        kwargs = dict(scheme='x402', asset='USDC', agent_id=old['request'].get('agentId'))
        result = asyncio.run(worker.download_async(**kwargs)) if asynchronous else worker.download(**kwargs)
    assert result['state'] == 'PAYMENT_PENDING' and len(calls) == 2
    saved = ActivityMapClient(tmp_path)._load(old['operation_id'])
    assert saved['purchase'] == json.loads(raw)
    amount = saved['purchase']['payment_challenge']['challenge']['amount']
    assert type(amount) is float and amount == 0.01
    for key in ('operation_id', 'request', 'request_bytes', 'request_sha256', 'recovery_secret', 'manifest_sha256'):
        assert saved[key] == old[key]


def test_legacy_integer_domain_and_checksum_remain_exact(tmp_path):
    legacy = json.loads((FIXTURE / 'initial-journal.json').read_text())
    value = legacy['payload']
    assert journal_bytes(value) == jcs_bytes(value)
    worker = ActivityMapClient(tmp_path)
    path = worker._path(value['operation_id'])
    path.write_bytes((FIXTURE / 'initial-journal.json').read_bytes()); path.chmod(0o600)
    assert worker._load(value['operation_id']) == value
    worker._save(value)
    assert path.read_bytes() == jcs_bytes(legacy)
    assert _read(path) == value
    unicode_value = {'\U0001f600': [True, None, 9007199254740991], '\ue000': {'日本': 'é'}}
    assert journal_bytes(unicode_value) == jcs_bytes(unicode_value)


@pytest.mark.parametrize('number', [0.01, 1.0, -0.0, 1e-50, 1e100, 5e-324])
def test_graph_numbers_roundtrip_without_relaxing_shared_jcs(tmp_path, number):
    path = tmp_path / 'journal.json'
    value = {'nested': [number], 'atomic': '10000'}
    with pytest.raises(ValueError): jcs_bytes(value)
    with pytest.raises(JournalPersistenceError): _write(path, value)
    assert not path.exists()
    _write(path, value, _encode=journal_bytes)
    actual = _read(path, _encode=journal_bytes)
    assert actual == value and type(actual['nested'][0]) is float
    assert math.copysign(1, actual['nested'][0]) == math.copysign(1, number)
    with pytest.raises(JournalError): _read(path)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf'), 9007199254740992, {'bad': object()}])
def test_invalid_save_keeps_original_atomic_file(tmp_path, bad):
    path = tmp_path / 'journal.json'
    _write(path, {'amount': 0.01}, _encode=journal_bytes)
    before = path.read_bytes()
    with pytest.raises(JournalPersistenceError): _write(path, {'amount': bad}, _encode=journal_bytes)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('replacement', ['0.02', '1e999', 'NaN', '"0.01"'])
def test_tampered_or_nonfinite_graph_number_rejected(tmp_path, replacement):
    path = tmp_path / 'journal.json'
    _write(path, {'amount': 0.01}, _encode=journal_bytes)
    path.write_bytes(path.read_bytes().replace(b'0.01', replacement.encode()))
    with pytest.raises(JournalError): _read(path, _encode=journal_bytes)


def test_atomic_replace_failure_preserves_saved_graph(tmp_path, monkeypatch):
    import mayim_agent.immediate_visit_journal as storage
    path = tmp_path / 'journal.json'
    _write(path, {'amount': 0.01}, _encode=journal_bytes)
    before = path.read_bytes()
    def fail(*args): raise OSError('synthetic replace failure')
    monkeypatch.setattr(storage.os, 'replace', fail)
    with pytest.raises(JournalPersistenceError): _write(path, {'amount': 0.02}, _encode=journal_bytes)
    assert path.read_bytes() == before and list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('action', ['resume', 'status', 'replacement'])
def test_exact_legacy_grant_fixture_unchanged(tmp_path, action):
    raw = (FIXTURE / 'legacy-grant-journal.json').read_bytes()
    old = json.loads(raw)['payload']
    worker = ActivityMapClient(tmp_path)
    path = worker._path(old['operation_id']); path.write_bytes(raw); path.chmod(0o600)
    assert worker._load(old['operation_id']) == old
    with pytest.raises(ActivityMapError) as error:
        getattr(worker, action)(old['operation_id'], **({'decision':'decline'} if action == 'replacement' else {}))
    assert error.value.code == 'GRANT_NOT_SUPPORTED' and path.read_bytes() == raw
