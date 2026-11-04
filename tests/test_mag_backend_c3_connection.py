"""Actual fixed c3 producer and owners connected through SDK closed consumers."""
import hashlib
import json
import os
import subprocess
from pathlib import Path
import pytest
from mayim_agent.activity_map import ActivityMapClient,ActivityMapError
from test_mag_activity_map_http import Bridge,client

pytestmark=pytest.mark.skipif(not os.environ.get('MAG_BACKEND_C3_ROOT'),reason='Fixed Backend c3 required')


@pytest.mark.parametrize('stop_during_payment',[True,False])
def test_real_producer_stop_replacement_and_verified_saved_bytes(tmp_path,stop_during_payment):
    bridge=Bridge('activity-map-producer-http.mjs')
    try:
        worker=client(bridge,tmp_path)
        bridge.command(action='fault',stopAtPayment=stop_during_payment,failPart=not stop_during_payment)
        if stop_during_payment:
            first=worker.download(scheme='x402',asset='USDC');op=first['operation_id']
            assert first['state']=='REPLACEMENT_REQUIRED'
        else:
            with pytest.raises(ActivityMapError) as error:worker.download(scheme='x402',asset='USDC')
            op=error.value.recovery['operation_id'];bridge.command(action='stop')
        saved=worker._load(op);old_dir=worker.directory/op/saved['generation']
        original={p.name:p.read_bytes() for p in old_dir.iterdir()} if old_dir.exists() else {}
        if not stop_during_payment:assert 'manifest.json' in original
        bridge.command(action='fault');before=bridge.command(action='counts')
        restarted=ActivityMapClient(worker.directory,transport=bridge,clock=worker.clock,sleep=lambda n:None)
        state=restarted.resume(op)
        assert state['state']=='REPLACEMENT_REQUIRED' and state['generation']==before['old']
        replacement=state['replacement'];assert replacement['generation']==before['new']
        assert replacement['difference']['activity_count_delta']==-1
        assert before['reader_page']['items'][0]['id']==before['retained']
        assert len(before['reader_page']['items'])==1
        result=restarted.replacement(op,decision='accept',generation=replacement['generation'],manifest_sha256=replacement['manifest_sha256'])
        assert result['state']=='COMPLETE' and result['generation']==before['new']
        assert result['expires_at']==saved['purchase']['expires_at']
        current=restarted._load(op)
        for key in ('request','request_bytes','payment_identity','payment_headers'):
            assert current[key]==saved[key]
        assert current['purchase']['confirmed_at']==saved['purchase']['confirmed_at']
        from datetime import datetime
        assert (datetime.fromisoformat(result['expires_at'])-datetime.fromisoformat(current['purchase']['confirmed_at'])).total_seconds()==86400
        directory=worker.directory/op/result['generation'];assert directory!=old_dir
        for name,raw in original.items():assert (old_dir/name).read_bytes()==raw
        raw=(directory/'manifest.json').read_bytes();assert hashlib.sha256(raw).hexdigest()==replacement['manifest_sha256']
        manifest=json.loads(raw);data=[];parts=0
        for desc in manifest['parts']:
            catalog=(directory/desc['part_id']).read_bytes();parts+=1
            assert len(catalog)==desc['bytes'] and hashlib.sha256(catalog).hexdigest()==desc['sha256']
            for item in json.loads(catalog)['parts']:
                payload=(directory/item['part_id']).read_bytes();parts+=1
                assert len(payload)==item['bytes'] and hashlib.sha256(payload).hexdigest()==item['sha256']
                data.append(payload.decode('utf-8'))
        assert result['verified_parts']==parts
        joined='\n'.join(data)
        assert before['retained'] in joined and before['removed'] not in joined and before['added'] not in joined
        after=bridge.command(action='counts')
        assert after['settlements']==after['payment_bindings']==1 and len(worker.payment_runtime.signs)==1
        assert after['purchases'][0]['payment_digest']==before['purchases'][0]['payment_digest']
        assert sum(x['path'].endswith('/complete') for x in after['calls'][len(before['calls']):])==1
    finally:bridge.stop()


def test_actual_taskget_errors_reach_all_four_closed_sdk_consumers():
    from mayim_agent.immediate_visit_transport import ImmediateVisitTransport,ImmediateVisitRawResponse,ImmediateVisitAPIError,ImmediateVisitTransportError
    from mayim_agent.endpoint_choice_reason_transport import EndpointChoiceTransport,EndpointChoiceAPIError,EndpointChoiceError
    from mayim_agent.paid_service_trial_transport import PaidServiceTrialTransport,PaidServiceTrialRawResponse,PaidServiceTrialAPIError
    from mayim_agent.structured_response_transport import StructuredResponseTransport,StructuredResponseAPIError
    from urllib.parse import urlencode
    script=Path(__file__).parent/'fixtures/mag/task-errors-c3.mjs'
    output=subprocess.run(['node',str(script)],capture_output=True,text=True,check=True,timeout=30)
    rows=json.loads(next(x.removeprefix('SDK_C3_TASK_ERRORS ') for x in output.stdout.splitlines() if x.startswith('SDK_C3_TASK_ERRORS ')))
    assert len(rows)==16
    for row in rows:
        family=row['family'];raw_type=PaidServiceTrialRawResponse if family=='paid' else ImmediateVisitRawResponse
        body=json.dumps(row['value']).encode()
        def exchange(*args,**kwargs):return raw_type(row['status'],row['headers'],body)
        transport,error={
            'choice':(lambda:EndpointChoiceTransport(exchange=exchange,version='v2_4'),EndpointChoiceAPIError),
            'immediate':(lambda:ImmediateVisitTransport(exchange=exchange,version='v3_2'),ImmediateVisitAPIError),
            'paid':(lambda:PaidServiceTrialTransport(exchange=exchange,version='v3_2'),PaidServiceTrialAPIError),
            'structured':(lambda:StructuredResponseTransport(exchange=exchange,definition_version='3.2.0'),StructuredResponseAPIError),
        }[family]
        adapter=transport()
        if family=='choice' and row['status']==503:error=EndpointChoiceError
        if family=='immediate' and row['status']==503:error=ImmediateVisitTransportError
        with pytest.raises(error) as raised:
            adapter._once('GET','/api/agent/tasks'+('/task_missing' if 'task_id' in row else ''),query=urlencode(row['query']))
        assert raised.value.status_code==row['status']
        if family in ('choice','immediate') and row['status']==503:
            assert raised.value.code=='TRANSPORT_ERROR' and raised.value.request_bytes_sent is True
        else:assert raised.value.public_error_code==row['value']['code']
        # The same transport still rejects an added wire field.
        invalid=dict(row['value'],unknown_field=True);body=json.dumps(invalid).encode()
        with pytest.raises(Exception) as rejected:adapter._once('GET','/api/agent/tasks',query=urlencode(row['query']))
        assert rejected.value.code=='RESPONSE_INVALID'


@pytest.mark.parametrize('change',[{'schema_version':'ln_church.task_error.immediate_visit.v3_1'},
    {'schema_version':'attacker.v3_2'},{'code':'invalid_request'},{'extra':True}])
def test_immediate_32_outage_stays_closed(change):
    from mayim_agent.immediate_visit_transport import ImmediateVisitTransport,ImmediateVisitRawResponse,ImmediateVisitTransportError
    payload=dict(schema_version='ln_church.task_error.immediate_visit.v3_2',code='temporarily_unavailable',message='Unavailable',request_id='synthetic')
    payload.update(change)
    adapter=ImmediateVisitTransport(version='v3_2',exchange=lambda *a,**k:ImmediateVisitRawResponse(503,{},json.dumps(payload).encode()))
    with pytest.raises(ImmediateVisitTransportError) as error:adapter._once('GET','/api/agent/tasks')
    assert error.value.code=='RESPONSE_INVALID'
