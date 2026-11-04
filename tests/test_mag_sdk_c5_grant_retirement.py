"""Fixed a75a82d §8: retire Graph Grant use while preserving saved history."""
import asyncio
import hashlib
import json

import pytest

from mayim_agent.activity_map import ActivityMapError
from mayim_agent.activity_map_payment import _proof
from test_mag_activity_map import Server


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('scheme,asset', [('grant', 'USDC'), ('x402', 'GRANT_CREDIT')])
def test_new_grant_rejected_before_network_or_journal(tmp_path, asynchronous, scheme, asset):
    server = Server()
    worker = server.connect(tmp_path)
    worker.payment_runtime = object()  # Any signer invocation would fail.
    with pytest.raises(ActivityMapError) as error:
        if asynchronous:
            asyncio.run(worker.download_async(scheme=scheme, asset=asset))
        else:
            worker.download(scheme=scheme, asset=asset)
    assert error.value.code == 'GRANT_NOT_SUPPORTED'
    assert server.calls == []
    assert list(worker.directory.iterdir()) == []


def test_payment_adapter_rejects_grant_proof_without_leaking_it():
    with pytest.raises(ActivityMapError) as error:
        _proof({}, {'paymentOverride': {'type': 'grant', 'token': 'synthetic-private-token'}})
    assert error.value.code == 'GRANT_NOT_SUPPORTED'
    assert 'synthetic-private-token' not in str(error.value)


@pytest.mark.parametrize('action', ['status', 'resume', 'replacement'])
@pytest.mark.parametrize('marker', ['scheme', 'rail', 'network', 'asset'])
def test_saved_grant_is_retained_but_never_replayed(tmp_path, action, marker):
    server = Server()
    server.pending = True
    worker = server.connect(tmp_path)
    operation = worker.download(scheme='x402', asset='USDC')['operation_id']
    journal = worker._load(operation)
    if marker == 'scheme':
        journal['request']['scheme'] = 'grant'
        journal['request_bytes'] = json.dumps(journal['request'], separators=(',', ':'))
        journal['request_sha256'] = hashlib.sha256(journal['request_bytes'].encode()).hexdigest()
    else:
        journal['payment_identity'] = {marker: {'rail': 'grant', 'network': 'offchain:grant',
                                               'asset': 'GRANT_CREDIT'}[marker]}
    journal['payment_headers'] = {'X-MAG-Payment-Override': 'synthetic-private-proof'}
    worker._save(journal)
    before = {p.relative_to(worker.directory): p.read_bytes()
              for p in worker.directory.rglob('*') if p.is_file()}
    calls = list(server.calls)
    worker.payment_runtime = object()
    with pytest.raises(ActivityMapError) as error:
        getattr(worker, action)(operation, **({'decision': 'decline'} if action == 'replacement' else {}))
    assert error.value.code == 'GRANT_NOT_SUPPORTED'
    assert 'synthetic-private-proof' not in str(error.value)
    assert server.calls == calls
    assert worker._load(operation) == journal
    assert {p.relative_to(worker.directory): p.read_bytes()
            for p in worker.directory.rglob('*') if p.is_file()} == before
