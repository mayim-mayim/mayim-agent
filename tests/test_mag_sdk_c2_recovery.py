"""DC T-SDK-C1-T01/G01: actual owners, synthetic loss and local disk damage."""
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
from mayim_agent.activity_map import ActivityMapClient, ActivityMapError
from mayim_agent.offer_registration import OfferRegistrationClient, OfferRegistrationError
from mayim_agent.immediate_visit_transport import ImmediateVisitTransport, ImmediateVisitRawResponse
from test_mag_task_http import Bridge as TaskBridge, register, economics
from test_mag_activity_map_http import Bridge, client
from test_offer_registration import signature

pytestmark=pytest.mark.skipif(not os.environ.get('MAG_BACKEND_C3_ROOT'),reason='Fixed Backend c3 required')


def test_legacy_choice_23_read_keeps_original_pack():
    bridge=TaskBridge(activity_epoch=False)
    try:
        _,result,op,cli=register(bridge,'choice','A',version='2.3.0')
        bridge.send(dict(control='economic',family='choice',terms=economics('choice','B')))
        proof=cli.get_registration_challenge(op.task_type,op.operation_ref,op.payer).sign(signature,now=cli._clock())
        assert cli.read_registration(proof).result==result
        assert result['schema_version']=='ln_church.task_offer_create_response.endpoint_choice_reason.v2_3'
        assert bridge.send({'control':'counts'})['settles']==1
    finally:bridge.close()


def test_choice_24_status_read_remains_read_only():
    bridge=TaskBridge()
    try:
        _,result,op,cli=register(bridge,'choice','A')
        bridge.send(dict(control='configure',confirmed=False))
        from mayim_agent.crypto.evm import LocalKeyAdapter
        pending=cli.prepare_registration(json.loads(op._body)).sign(LocalKeyAdapter('17'*32),now=cli._clock())
        outcome=cli.submit_registration(pending)
        assert outcome.kind=='STATUS' and outcome.result is None
        proof=cli.get_registration_challenge(pending.task_type,pending.operation_ref,pending.payer).sign(signature,now=cli._clock())
        before=bridge.send({'control':'counts'})
        assert cli.read_registration(proof).kind=='STATUS'
        assert bridge.send({'control':'counts'})['settles']==before['settles']
    finally:bridge.close()


def test_choice_saved_registration_read_after_lost_commit_response_and_B():
    bridge=TaskBridge()
    try:
        calls=[];saved=[]
        def lose(method,path,query,headers,body,timeout=20,**kw):
            response=bridge(method,path,query,headers,body,timeout,**kw)
            calls.append((path,dict(headers)))
            if path=='/api/bazaar/task-offers' and response.status_code==200:
                saved.append(json.loads(response.body))
                raise OSError('Synthetic committed response loss')
            return response
        # register() prepares/signs through this proxy, with real owner commit.
        class Proxy:
            send=bridge.send
            __call__=staticmethod(lose)
        with pytest.raises(OfferRegistrationError):register(Proxy(),'choice','A')
        assert len(saved)==1
        result=saved[0];ref=result['registration_intent_id']
        from eth_account import Account
        payer=Account.from_key('17'*32).address
        bridge.send(dict(control='economic',family='choice',terms=economics('choice','B')))
        cli=OfferRegistrationClient(transport=ImmediateVisitTransport(exchange=lose),wall_time=lambda:bridge.send({'control':'counts'})['now']/1000)
        before=bridge.send({'control':'counts'});start=len(calls)
        proof=cli.get_registration_challenge('endpoint_choice_reason.v2',ref,payer).sign(signature,now=cli._clock())
        recovered=cli.read_registration(proof)
        assert recovered.kind=='COMMITTED' and recovered.result==result
        assert result['economic_terms_revision']=='choice-A'
        assert bridge.send({'control':'counts'})['settles']==before['settles']==1
        assert all(not any(k.lower()=='payment-signature' for k in h) for _,h in calls[start:])
        # Mutation tests retain the authenticated proof/binding. Full schema,
        # inner task/operation and digest syntax remain closed.
        envelope=dict(schema_version='ln_church.registration_read_result.v1',profile='V189_ENDPOINT_CHOICE_REASON',
            operation_ref=ref,kind='COMMITTED',result=result,status=None)
        for mutate in [lambda v:v.update(extra=True),lambda v:v.update(operation_ref='different'),
                lambda v:v.update(profile='OTHER'),lambda v:v['result'].update(task_type='immediate_http_visit.v3'),
                lambda v:v['result'].update(schema_version='attacker.v2_4'),
                lambda v:v['result'].update(registration_intent_id='different'),
                lambda v:v['result'].update(terms_digest='invalid'),
                lambda v:v['result'].update(manifest_url=result['manifest_url'].replace('2.4.0','2.3.0')),
                lambda v:v['result'].update(activity_map_policy_id='invented')]:
            bad=copy.deepcopy(envelope);mutate(bad)
            transport=ImmediateVisitTransport(exchange=lambda *a,**k:ImmediateVisitRawResponse(200,{},json.dumps(bad).encode()))
            with pytest.raises(OfferRegistrationError):OfferRegistrationClient(transport=transport,wall_time=cli._clock).read_registration(proof)
    finally:bridge.close()


def unconfirmed_complete(bridge,tmp_path):
    worker=client(bridge,tmp_path);bridge.command(action='fault',dropComplete=True)
    with pytest.raises(ActivityMapError) as error:worker.download(scheme='x402',asset='USDC')
    op=error.value.recovery['operation_id'];saved=worker._load(op)
    assert saved['complete_notification']=='sent_unconfirmed'
    assert bridge.command(action='counts')['purchases'][0]['state']=='ACQUIRING'
    return worker,op,saved


def damage(worker,op,kind,mode):
    saved=worker._load(op);directory=worker.directory/op/saved['generation'];manifest=directory/'manifest.json'
    desc=json.loads(manifest.read_bytes())['parts'][0];catalog=directory/desc['part_id']
    data=directory/json.loads(catalog.read_bytes())['parts'][0]['part_id']
    target={'manifest':manifest,'catalog':catalog,'data':data}[kind];expected=target.read_bytes()
    if mode=='missing':target.unlink()
    else:target.write_bytes(b'x'*len(expected))
    return target,expected


@pytest.mark.parametrize('kind',['manifest','catalog','data'])
@pytest.mark.parametrize('mode',['missing','corrupt'])
def test_acquiring_after_unsent_complete_revalidates_selectively(tmp_path,kind,mode):
    bridge=Bridge()
    try:
        worker,op,saved=unconfirmed_complete(bridge,tmp_path);target,expected=damage(worker,op,kind,mode)
        bridge.command(action='fault');before=bridge.command(action='counts')
        restarted=ActivityMapClient(worker.directory,transport=bridge,clock=worker.clock,sleep=lambda n:None)
        result=restarted.resume(op);after=bridge.command(action='counts')
        assert result['state']=='COMPLETE' and target.read_bytes()==expected
        assert after['settlements']==1 and len(worker.payment_runtime.signs)==1
        current=restarted._load(op)
        for key in ('request','request_bytes','payment_identity','payment_headers','generation','manifest_sha256'):
            assert current[key]==saved[key]
        assert current['purchase']['expires_at']==saved['purchase']['expires_at']
        assert current['purchase']['confirmed_at']==saved['purchase']['confirmed_at']
        paths=[x['path'] for x in after['calls'][len(before['calls']):] if x['method']=='GET']
        assert paths==['/api/agent/activity-map/v1/purchases/'+op+('/manifest' if kind=='manifest' else '/parts/'+target.name)]
    finally:bridge.stop()


@pytest.mark.parametrize('end',['stop','expiry','unavailable'])
def test_unconfirmed_revalidation_never_converts_failure_to_success(tmp_path,end):
    bridge=Bridge()
    try:
        worker,op,saved=unconfirmed_complete(bridge,tmp_path);damage(worker,op,'data','corrupt')
        bridge.command(action='fault',failPart=end=='unavailable')
        if end=='stop':bridge.command(action='revoke',replacement=False)
        if end=='expiry':bridge.command(action='advance',ms=86400001)
        before=bridge.command(action='counts')
        restarted=ActivityMapClient(worker.directory,transport=bridge,clock=worker.clock,sleep=lambda n:None)
        if end=='unavailable':
            with pytest.raises(ActivityMapError):restarted.resume(op)
        else:assert restarted.resume(op)['state'] in ('UNAVAILABLE','EXPIRED_UNCONFIRMED')
        after=bridge.command(action='counts')
        assert after['settlements']==1
        assert not any(x['path'].endswith('/complete') for x in after['calls'][len(before['calls']):])
    finally:bridge.stop()


@pytest.mark.parametrize('server_complete',[False,True])
def test_complete_uncertainty_preserves_valid_cache_and_terminal_rights(tmp_path,server_complete):
    bridge=Bridge()
    try:
        worker=client(bridge,tmp_path)
        bridge.command(action='fault',loseComplete=server_complete,dropComplete=not server_complete)
        with pytest.raises(ActivityMapError) as error:worker.download(scheme='x402',asset='USDC')
        op=error.value.recovery['operation_id']
        if server_complete:target,expected=damage(worker,op,'data','corrupt')
        bridge.command(action='fault');before=bridge.command(action='counts')
        restarted=ActivityMapClient(worker.directory,transport=bridge,clock=worker.clock,sleep=lambda n:None)
        assert restarted.resume(op)['state']=='COMPLETE'
        new=bridge.command(action='counts')['calls'][len(before['calls']):]
        assert not any('/parts/' in x['path'] or x['path'].endswith('/manifest') for x in new)
        assert sum(x['path'].endswith('/complete') for x in new)==(0 if server_complete else 1)
        if server_complete:assert target.read_bytes()!=expected # No renewed payload right.
    finally:bridge.stop()
