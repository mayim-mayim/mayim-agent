"""Diagnostic: shared c4 support's B01/B02 are failures of Product operations.

An expected diagnostic is not a successful Scheduled completion or six-family
E01. All registered Tasks and Claims come from real Hono/default owners.
"""
import json,os,subprocess,urllib.request,urllib.error
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlsplit
import pytest
from mayim_agent.offer_registration import OfferRegistrationClient,OfferRegistrationError
from mayim_agent.immediate_visit_transport import ImmediateVisitTransport,ImmediateVisitRawResponse
from mayim_agent.task_v2_client import AgentTaskV2Client,CompletionOutcomeUnknownError
from mayim_agent.task_v2_transport import TaskV2Transport,TaskV2RawResponse,TaskV2APIError
from mayim_agent.task_v2_models import ScheduledTaskClaimCredential,ScheduledCompletionReport
from mayim_agent.task_journal import TaskJournal
from mayim_agent.task_discovery_models import TaskDiscoveryPage
from mayim_agent.task_v2_contract import canonical_completion_bytes

pytestmark=pytest.mark.skipif(not os.environ.get('LN_CHURCH_C4_SUPPORT_ROOT'),reason='Fixed c4 support checkout is required')


def test_sdk_diagnoses_shared_c4_scheduled_boundaries_without_success_stub(tmp_path):
    log=(tmp_path/'host.stderr').open('w')
    p=subprocess.Popen(['node',str(Path(__file__).parent/'fixtures/v1816-c4/support-http.mjs')],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True)
    def read(prefix):
        while True:
            line=p.stdout.readline();assert line,'Support host stopped'
            if line.startswith(prefix):return json.loads(line[len(prefix):])
    info=read('SDK_SUPPORT_READY ');access=json.loads(Path(info['access_file']).read_text());calls=[]
    def raw(method,path,query=None,headers=None,body=b'',timeout=20):
        try:r=urllib.request.urlopen(urllib.request.Request(access['origin']+path+('?' + query if query else ''),
            method=method,headers=dict(headers or {},**access['headers']),data=body if method!='GET' else None),timeout=timeout)
        except urllib.error.HTTPError as error:r=error
        with r:
            calls.append((method,path,r.status));return TaskV2RawResponse(r.status,dict(r.headers),r.read())
    def local(path,body=None):
        response=raw('GET' if body is None else 'POST','/_test/'+path,headers={'Content-Type':'application/json'},body=json.dumps(body).encode())
        assert response.status_code==200
        return json.loads(response.body)
    def now():return datetime.fromtimestamp(local('state')['clock']/1000,timezone.utc)
    client=AgentTaskV2Client(definition_version='1.1.0',transport=TaskV2Transport(definition_version='1.1.0',exchange=raw),utcnow=now)
    def offer_exchange(*args):
        value=raw(*args);return ImmediateVisitRawResponse(value.status_code,value.headers,value.body)
    try:
        with pytest.raises(OfferRegistrationError):
            OfferRegistrationClient(transport=ImmediateVisitTransport(exchange=offer_exchange)).get_economic_offers('scheduled')
        page=TaskDiscoveryPage.model_validate(json.loads(raw('GET','/api/agent/tasks').body))
        assert {row['task_id'] for row in info['tasks']} <= {task.task_id for task in page.tasks}
        saved=[]
        for n,row in enumerate(info['tasks']):
            task=client.get_task(row['task_id']);assert task.economic_terms_revision==row['terms']['economic_terms_revision']
            credentials=[client.claim_task(task.task_id,agent_id='sdk-'+str(n)+'-'+str(i),reward_address='0x'+format(n*100+i+1,'040x')) for i in range(task.minimum_counted_claims)]
            claim=credentials[0]
            assert claim.economic_terms==row['terms']
            restored=ScheduledTaskClaimCredential._from_private_file_payload(claim._to_private_file_payload())
            assert restored.terms_digest==claim.terms_digest and restored.economic_terms==claim.economic_terms
            journal=TaskJournal(tmp_path/('scheduled-'+str(n)+'.json'),task_id=claim.task_id,
                local_claim_credential_handle=claim.local_claim_credential_handle,task_type=claim.task_type,
                task_definition_version=claim.task_definition_version,task_definition_digest=claim.task_definition_digest)
            journal.create();saved.append((claim,journal))
        first_pid=info['backend_pid'];p.stdin.write('{"action":"restart"}\n');p.stdin.flush()
        restarted=read('SDK_SUPPORT_REPLY ');assert restarted['backend_pid']!=first_pid
        access=json.loads(Path(restarted['access_file']).read_text())
        for row in info['tasks']:
            assert client.get_task(row['task_id']).economic_terms_revision==row['terms']['economic_terms_revision']
        start=max(datetime.fromisoformat(c.scheduled_at.replace('Z','+00:00')).timestamp()*1000 for c,_ in saved)
        local('clock',{'advance_ms':int(start-local('state')['clock']+1000)})
        for claim,journal in saved:client.get_readiness(claim,journal=journal)
        local('work',{'kind':'MANIFEST_PROJECTION'})
        for n,(claim,journal) in enumerate(saved):
            readiness=client.get_readiness(claim,journal=journal)
            url=urlsplit(claim._manifest_url_value());response=raw('GET',url.path,url.query)
            assert response.status_code==200
            # Target response is an explicit external HTTP double; manifest is real.
            manifest=canonical_completion_bytes(json.loads(response.body))
            journal.start_manifest_attempt();journal.bind_verified_manifest(readiness.manifest_sha256,1,manifest_bytes=manifest)
            journal.mark_target_attempt_started(0);journal.record_target_result(0,outcome='http_response',http_status=200,elapsed_ms=100)
            report=ScheduledCompletionReport(submission_id='sub_'+str(n+1)*32,
                task_definition_version=claim.task_definition_version,task_definition_digest=claim.task_definition_digest,
                manifest_sha256=readiness.manifest_sha256,manifest_fetch=dict(outcome='retrieved',http_status=200,observed_sha256=readiness.manifest_sha256,elapsed_ms=25),
                results=[dict(position=0,target_url='https://example.com/a',outcome='http_response',http_status=200,elapsed_ms=100)],
                completed_at=now().strftime('%Y-%m-%dT%H:%M:%SZ'))
            journal.freeze_report(report.canonical_bytes(),submission_id=report.submission_id,manifest_fetch_outcome='retrieved')
            before=len(calls)
            with pytest.raises((TaskV2APIError,CompletionOutcomeUnknownError)):
                client.complete_task(claim,report,journal=journal)
            assert any(method=='POST' and status==503 for method,path,status in calls[before:])
            with pytest.raises(TaskV2APIError) as missing:
                client.get_submission_status(claim.task_id,report.submission_id)
            assert missing.value.status_code==404
            assert journal.load().state=='REPORT_FROZEN'
    finally:
        if p.poll() is None:p.stdin.write('{"action":"close"}\n');p.stdin.flush();p.wait(timeout=15)
        log.close()
