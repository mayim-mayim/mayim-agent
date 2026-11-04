"""Actual fixed-mAG-c1 HTTP, SQLite and object persistence; no seeded COMMITTED Tasks.

External purchase/finality and APS preparation are the producer's asset-free
doubles. Evaluation/payout are outside this listener. Run with LN_CHURCH_C4_ROOT.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request

import pytest
from mayim_agent import structured_response_contract as c
from mayim_agent.structured_response_client import StructuredResponseClient
from mayim_agent.structured_response_journal import StructuredResponseJournal
from mayim_agent.structured_response_models import StructuredResponseClaimCredential
from mayim_agent.structured_response_transport import StructuredResponseTransport, StructuredResponseError
from mayim_agent.immediate_visit_transport import ImmediateVisitRawResponse

ROOT = (os.environ.get('MAG_BACKEND_C3_ROOT') or os.environ.get('MAG_BACKEND_C2_ROOT') or os.environ.get('MAG_BACKEND_C1_ROOT'))
pytestmark = pytest.mark.skipif(not ROOT, reason='Fixed mAG Backend c1/c2/c3 checkout is required')


def test_mag_structured_current_B_and_saved_A_process_recovery(tmp_path, monkeypatch):
    key = tmp_path / 'key'
    key.write_bytes(os.urandom(32)); key.chmod(0o600)
    monkeypatch.setenv('MAG_STRUCTURED_RESPONSE_KEY_FILE', str(key))
    (tmp_path / 'journals').mkdir(mode=0o700)
    env = dict(os.environ, LN_TEST_STATE_DIR=str(tmp_path / 'backend'))
    script = Path(__file__).parent / 'fixtures/mag/structured-http.mjs'
    calls = []
    def read(server, prefix):
        while True:
            line = server.stdout.readline()
            assert line, 'c4 fixture stopped before response'
            if line.startswith(prefix):
                return json.loads(line[len(prefix):])
    def start():
        server = subprocess.Popen(['node', str(script)], env=env, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return server, read(server, 'SDK_MAG_READY ')
    def stop(server):
        server.stdin.write('{"action":"close"}\n'); server.stdin.flush()
        assert server.wait(timeout=10) == 0
    server, info = start()
    access = json.loads(Path(info['access_file']).read_text())
    def exchange(method, path, query, headers, body, timeout):
        url = access['origin'] + path + ('?' + query if query else '')
        try:
            response = urllib.request.urlopen(urllib.request.Request(url, method=method,
                headers=dict(headers, **access['headers']), data=body if method != 'GET' else None), timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            calls.append({'method': method, 'path': path, 'status': response.status})
            return ImmediateVisitRawResponse(response.status, dict(response.headers), response.read())
    def client(version):
        return StructuredResponseClient(transport=StructuredResponseTransport(exchange=exchange, definition_version=version))
    saved = []
    try:
        from mayim_agent.task_discovery_models import TaskDiscoveryPage
        server.stdin.write('{"action":"discovery"}\n'); server.stdin.flush()
        page=TaskDiscoveryPage.model_validate(read(server,'SDK_MAG_REPLY '))
        assert {row['task_id'] for row in info['tasks']} <= {row.task_id for row in page.tasks}
        for n, row in enumerate(info['tasks']):
            api = client(row['version'])
            task = api.get_task(row['task_id']); assert task.supported
            op = api.claim(task.task_id, 'c4-sdk', '0x' + format(n + 1, '040x'), 'c4-' + str(n),
                           journal_directory=tmp_path / 'journals')
            if row['version'] == '3.2.0':
                assert op.claim.terms_snapshot == c.terms_snapshot(task.model_dump())
                for field in ('economic_terms_revision', 'terms_digest', 'task_definition_digest'):
                    bad = op.claim._private_payload()
                    bad[field] = 'changed' if field == 'economic_terms_revision' else '0' * 64
                    with pytest.raises(ValueError):
                        StructuredResponseClaimCredential.model_validate(bad)
            entries = [dict(item_id=i['item_id'], status='unknown', value=None, reason='Synthetic unknown.')
                       for i in task.items if i['required']]
            if row['kind'] == 'CUSTOM':
                entries[0].update(status='answered', value={'custom': True}, reason='')
            report = api.prepare_answer(journal=op.journal, shared_assumptions={'must':'','volume':'','outcome':''}, items=entries)
            result = api.submit(journal=op.journal, max_post_requests=1)
            assert result.state == 'accepted', result.model_dump()
            status = api.get_submission_status(journal=op.journal)
            assert status.report_sha256 == report.report_sha256
            saved.append((row, op.journal.handle, report.report_sha256, op.claim.terms_digest))
        a = next(row for row in saved if row[0]['version'] == '3.2.0' and row[0]['kind'] == 'CUSTOM')
        server.stdin.write('{"action":"switch_B"}\n'); server.stdin.flush()
        b = read(server, 'SDK_MAG_REPLY ')
        btask = client('3.2.0').get_task(b['task_id'])
        assert btask.economic_terms_revision == 'sdk-mag-B' and btask.capacity_total == 7
        assert btask.reward['normal_amount_atomic'] == '123456'
        bop = client('3.2.0').claim(btask.task_id, 'c4-sdk', '0x'+'f'*40, 'c4-B', journal_directory=tmp_path/'journals')
        assert bop.claim.terms_digest == btask.terms_digest != a[3]
        entries = [dict(item_id=i['item_id'], status='unknown', value=None, reason='Synthetic unknown.')
                   for i in btask.items if i['required']]
        client('3.2.0').prepare_answer(journal=bop.journal, shared_assumptions={'must':'','volume':'','outcome':''}, items=entries)
        assert client('3.2.0').submit(journal=bop.journal).state == 'accepted'
        # Evaluate A after B exists, then B, at the fixed 0.70 reduced boundary.
        ajournal = StructuredResponseJournal.open(tmp_path/'journals', a[1])
        for journal in (ajournal, bop.journal):
            claim = journal.load_claim()
            server.stdin.write(json.dumps(dict(action='evaluate',task_id=claim.task_id,execution_id=claim.execution_id,q='0.7'))+'\n'); server.stdin.flush()
            read(server, 'SDK_MAG_REPLY ')
            status = client('3.2.0').get_submission_status(journal=journal)
            assert status.entitlement['amount_atomic'] == str(int(claim.terms_snapshot['reward']['normal_amount_atomic'])*7//10)
            bad = status.model_dump(); bad['entitlement']['amount_atomic'] = '1'
            with pytest.raises(StructuredResponseError):
                client('3.2.0')._bound(type(status),bad,claim,journal.load_report())
        first_pid = info['pid']; stop(server)
        server, restarted = start(); assert restarted['pid'] != first_pid
        access = json.loads(Path(restarted['access_file']).read_text())
        for row, handle, report_hash, terms in saved:
            journal = StructuredResponseJournal.open(tmp_path / 'journals', handle)
            before = len(calls)
            outcome = client(row['version']).recover_completion(journal=journal)
            assert outcome.state == 'accepted' and outcome.post_requests == 0
            assert outcome.status.report_sha256 == report_hash and journal.load_claim().terms_digest == terms
            assert all(x['method'] == 'GET' for x in calls[before:])
        old_a = client('3.2.0').get_task(a[0]['task_id'])
        assert old_a.terms_digest == a[3] and old_a.economic_terms_revision != 'sdk-mag-B'
        with pytest.raises(StructuredResponseError):
            client('3.0.0').get_task(btask.task_id)
    finally:
        if server.poll() is None:
            stop(server)
