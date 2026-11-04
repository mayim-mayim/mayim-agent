"""Actual original SDK journals and old accepted DB reopen under c4 in a new PID."""
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
from mayim_agent.structured_response_transport import StructuredResponseTransport
from mayim_agent.immediate_visit_transport import ImmediateVisitRawResponse

FIXTURES=Path(__file__).parent/'fixtures/v1816-c4'
pytestmark=pytest.mark.skipif(not all(os.environ.get(k) for k in ('LN_CHURCH_C4_ROOT','LN_CHURCH_C2_ROOT','LN_SDK_PARENT_ROOT')),
    reason='Fixed Backend c2/c4 and original SDK parent checkouts are required')


def test_original_sdk_accepted_and_unknown_journals_read_c4_after_expiry(tmp_path,monkeypatch):
    keys=tmp_path/'keys';keys.mkdir(mode=0o700)
    key=keys/'key';key.write_bytes(os.urandom(32));key.chmod(0o600)
    journals=tmp_path/'journals';journals.mkdir(mode=0o700)
    monkeypatch.setenv('MAG_STRUCTURED_RESPONSE_KEY_FILE',str(key))
    env=dict(os.environ,HONDO_ROOT=os.environ['LN_CHURCH_C2_ROOT'],LN_TEST_STATE_DIR=str(tmp_path/'state'),PORT='0')
    log=(tmp_path/'host.stderr').open('w')
    server=subprocess.Popen(['node',str(FIXTURES/'legacy-producer-http.mjs')],env=env,stdout=subprocess.PIPE,stderr=log,text=True)
    try:
        info=json.loads(server.stdout.readline());origin='http://127.0.0.1:'+str(info['port'])
        def call(path,method='GET',body=None,headers=None):
            try:r=urllib.request.urlopen(urllib.request.Request(origin+path,method=method,headers=headers or {},data=body),timeout=20)
            except urllib.error.HTTPError as error:r=error
            with r:return ImmediateVisitRawResponse(r.status,dict(r.headers),r.read())
        produced=subprocess.run(['python3',str(FIXTURES/'legacy-sdk-producer.py'),origin,info['task_id'],str(journals)],
            env=dict(env,PYTHONPATH=os.environ['LN_SDK_PARENT_ROOT']+os.pathsep+os.environ.get('LN_SDK_TEST_DEPS','')),
            capture_output=True,text=True,check=True,timeout=45)
        old=json.loads(produced.stdout)
        assert str(Path(old['sdk_source']).resolve()).startswith(str(Path(os.environ['LN_SDK_PARENT_ROOT']).resolve()))
        before=json.loads(call('/__control','POST',b'{"advance":600001}',{'Content-Type':'application/json'}).body)
        assert before['accepted_count']==2 and before['rewards_paid_confirmed']==0
        old_pid=server.pid;server.terminate();assert server.wait(timeout=10)==0
        script=Path(os.environ['LN_CHURCH_C4_ROOT'])/'tests/template-survey-sharing/backend-c3/http-fixture.mjs'
        server=subprocess.Popen(['node',str(script)],env=env,stdout=subprocess.PIPE,stderr=log,text=True)
        line=server.stdout.readline();assert line;ready=json.loads(line)
        assert server.pid!=old_pid
        access=json.loads(Path(ready['access_file']).read_text());origin=access['origin'];calls=[]
        def exchange(method,path,query,headers,body,timeout):
            response=call(path,method,body if method!='GET' else None,dict(headers,**access['headers']))
            calls.append((method,response.status_code));return response
        client=StructuredResponseClient(transport=StructuredResponseTransport(exchange=exchange))
        for operation in old['operations']:
            journal=StructuredResponseJournal.open(journals,operation['handle'])
            report=journal.load_report();claim=journal.load_claim()
            with pytest.raises(ValueError):report.validate_binding(journal.load_task(),claim)
            start=len(calls);outcome=client.recover_completion(journal=journal)
            assert outcome.state=='accepted' and outcome.post_requests==0 and calls[start:]==[('GET',200)]
            assert outcome.status.report_sha256==operation['report_sha256']==report.report_sha256
            assert claim.terms_digest==operation['terms_digest'] and outcome.status.entitlement['state']=='PENDING'
            headers={'Content-Type':'application/json','X-LN-Task-Claim-Token':claim._claim_token_value(),
                     'Idempotency-Key':report.report['submission_id']}
            replay=exchange('POST',c.task_completion_path(claim.task_id),None,headers,report.canonical_bytes,20)
            assert replay.status_code==200 and json.loads(replay.body)['report_sha256']==report.report_sha256
            changed=report.report;changed['items'][0]['reason']+=' different'
            assert exchange('POST',c.task_completion_path(claim.task_id),None,headers,c.canonical_bytes(changed),20).status_code==409
            assert exchange('GET',c.task_status_path(claim.task_id,report.report['submission_id']),None,
                            {'X-LN-Task-Claim-Token':'A'*43},b'',20).status_code==401
    finally:
        if server.poll() is None:server.terminate();server.wait(timeout=10)
        log.close()
