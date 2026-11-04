"""SDK registration/read contracts against c4's actual Gateway and read owners.

Paid registration is an external test caller, not a new SDK Requester. EIP-3009
and EIP-712 signatures are real and use ephemeral unfunded local keys.
"""
import base64
import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from mayim_agent.crypto.evm import LocalKeyAdapter
from mayim_agent.offer_registration import OfferRegistrationClient, OfferRegistrationError, RegistrationReadChallenge
from mayim_agent.immediate_visit_transport import ImmediateVisitTransport, ImmediateVisitRawResponse

ROOT = os.environ.get('LN_CHURCH_C4_ROOT')
pytestmark = pytest.mark.skipif(not ROOT, reason='Fixed Backend c4 checkout is required')


def test_c4_three_registration_reads_saved_A_after_B_and_process_restart(tmp_path):
    env = dict(os.environ, PORT='0', LN_TEST_STATE_DIR=str(tmp_path/'backend'))
    def start():
        log = (tmp_path/'host.stderr').open('a')
        process = subprocess.Popen(['node', str(Path(ROOT)/'tests/template-survey-sharing/backend-c4/http-fixture.mjs')],
                                   env=env, stdout=subprocess.PIPE, stderr=log, text=True)
        while True:
            line = process.stdout.readline()
            assert line, 'c4 registration host stopped'
            if line.startswith('C4_READY '):
                ready = json.loads(line[len('C4_READY '):]); break
        access = json.loads(Path(ready['access_file']).read_text())
        return process, access, ready, log
    def stop(process, log):
        process.terminate(); assert process.wait(timeout=10) == 0; log.close()
    process, access, ready, log = start()
    calls = []
    public_registration_responses = []
    def exchange(method, path, query, headers, body, timeout):
        try:
            response = urllib.request.urlopen(urllib.request.Request(access['origin']+path,
                method=method, headers=dict(headers), data=body if method!='GET' else None), timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            calls.append((method, path, response.status, any(k.lower()=='payment-signature' for k in headers)))
            raw = response.read()
            if path == '/api/bazaar/task-offers' and response.status != 402:
                public_registration_responses.append(json.loads(raw))
            return ImmediateVisitRawResponse(response.status, dict(response.headers), raw)
    def local(path, body=None):
        raw = exchange('GET' if body is None else 'POST', '/_test/'+path, None,
                       {'Content-Type':'application/json', **access['headers']}, json.dumps(body).encode(), 20)
        assert raw.status_code == 200
        return json.loads(raw.body)
    def current_time():
        return local('state')['clock']/1000
    def sign(account):
        return lambda typed: '0x'+account.sign_message(encode_typed_data(full_message=typed)).signature.hex().removeprefix('0x')
    account = Account.create(); other = Account.create()
    payer = account.address.lower(); signer = LocalKeyAdapter(account.key.hex())
    cli = OfferRegistrationClient(transport=ImmediateVisitTransport(exchange=exchange), wall_time=current_time)
    saved = []
    try:
        for family, task_type in [('immediate','immediate_http_visit.v3'),('paid','paid_service_trial.v3'),('choice','endpoint_choice_reason.v2')]:
            original_terms = None
            for variant in ('A','B'):
                terms = cli.get_economic_offers(family)['offers'][0].copy(); terms.pop('economic_terms_digest')
                if variant == 'A': original_terms = copy.deepcopy(terms)
                if family == 'immediate':
                    request = dict(schema_version='ln_church.task_offer_create_request.immediate_visit.v3_1',task_type=task_type,
                        task_definition_version='3.1.0',economic_terms=terms,plan_id=terms['plan_id'],repeat_policy='allow',urls=['https://example.com/'])
                elif family == 'choice':
                    request = dict(schema_version='ln_church.task_offer_registration.endpoint_choice_reason.v2_3',task_type=task_type,
                        task_definition_version='2.3.0',economic_terms=terms,plan_id=terms['plan_id'],question='Choose a synthetic service',
                        candidates=[dict(candidate_id='one',url='https://example.com/',evaluator_description='Synthetic service')],
                        correct_candidate_ids=['one'],reveal_correct_set_after_answer=False)
                else:
                    request = dict(schema_version='ln_church.task_offer_create_request.paid_service_trial.v3_1',task_type=task_type,
                        task_definition_version='3.1.0',economic_terms=terms,plan_id=terms['plan_id'],repeat_policy='ALLOW_REPEAT',
                        **local('sample', {'payer':payer}))
                if family != 'paid':
                    operation = cli.prepare_registration(request).sign(signer, now=int(current_time()))
                    try:
                        result = cli.submit_registration(operation)
                    except OfferRegistrationError:
                        pytest.fail(f'{family} {variant}: {public_registration_responses[-1]}')
                else:
                    # Independent requester fixture: use the actual returned 402 terms.
                    raw = exchange('POST','/api/bazaar/task-offers',None,{'Content-Type':'application/json'},json.dumps(request).encode(),20)
                    assert raw.status_code == 402
                    challenge = json.loads(raw.body); accepted = challenge['accepts'][0]; now = int(current_time())
                    payload = signer.generate_eip3009_payload_atomic('USDC',accepted['amount'],accepted['payTo'],chain_id=8453,
                        token_address=accepted['asset'],valid_before=now+300,now=now)
                    payment = base64.b64encode(json.dumps(dict(x402Version=2,resource=challenge['resource'],accepted=accepted,payload=payload)).encode()).decode()
                    raw = exchange('POST','/api/bazaar/task-offers',None,{'Content-Type':'application/json','PAYMENT-SIGNATURE':payment},json.dumps(request).encode(),20)
                    assert raw.status_code == 200
                    result = json.loads(raw.body)
                    with pytest.raises(OfferRegistrationError):
                        cli.prepare_registration(request)
                ref = result['registration_intent_id']
                challenge = cli.get_registration_challenge(task_type, ref, payer)
                proof = challenge.sign(sign(account), now=current_time())
                read = cli.read_registration(proof)
                assert read.kind == 'COMMITTED' and read.result == result
                assert result['economic_terms_revision'] == terms['economic_terms_revision']
                saved.append((family,task_type,ref,result))
                bad_signature = challenge.sign(sign(other), now=current_time())
                with pytest.raises(OfferRegistrationError, match='invalid_recovery_proof'):
                    cli.read_registration(bad_signature)
                wrong_payer = cli.get_registration_challenge(task_type,ref,other.address).sign(sign(other),now=current_time())
                with pytest.raises(OfferRegistrationError, match='registration_recovery_unavailable'):
                    cli.read_registration(wrong_payer)
                for field, value in [('profile','OTHER'),('operationRef','00000000-0000-4000-8000-000000000000'),('payer',other.address.lower()),('audience','https://example.com/')]:
                    bad = json.loads(challenge._wire); bad['typed_data']['message'][field] = value
                    with pytest.raises((ValueError,OfferRegistrationError)):
                        RegistrationReadChallenge(bad,task_type,ref,payer,current_time())
                if variant == 'A':
                    changed = copy.deepcopy(terms)
                    changed.update(economic_terms_revision='sdk-c4-B',plan_id='opaque-label-B',
                                   registration_amount_atomic=str(int(terms['registration_amount_atomic'])+333333))
                    local('offer',{'family':family,'offers':[changed]})
            # Corrupt one actual stored authority at a time, then restore it.
            _, _, ref, original = saved[-2]
            db = sqlite3.connect(Path(ready['directory'])/'store.sqlite')
            pk = 'REGISTRATION_INTENT#'+ref
            raw = db.execute('SELECT v FROM rows WHERE t=? AND pk=? AND sk=?',('tasks',pk,'INTENT')).fetchone()[0]
            try:
                for field, value in [('economic_terms_revision','tampered'),('terms_digest','0'*64),('registration_amount_atomic','1')]:
                    bad = json.loads(raw); bad['published_result'][field] = value
                    db.execute('UPDATE rows SET v=? WHERE t=? AND pk=? AND sk=?',(json.dumps(bad),'tasks',pk,'INTENT')); db.commit()
                    proof = cli.get_registration_challenge(task_type,ref,payer).sign(sign(account),now=current_time())
                    with pytest.raises(OfferRegistrationError, match='registration_recovery_unavailable'):
                        cli.read_registration(proof)
            finally:
                db.execute('UPDATE rows SET v=? WHERE t=? AND pk=? AND sk=?',(raw,'tasks',pk,'INTENT')); db.commit(); db.close()
        old_pid = process.pid; stop(process,log)
        process,access,ready,log = start(); assert process.pid != old_pid
        before = local('state'); count = len(calls)
        for family,task_type,ref,result in saved:
            proof = cli.get_registration_challenge(task_type,ref,payer).sign(sign(account),now=current_time())
            assert cli.read_registration(proof).result == result
        after = local('state')
        assert after['calls'] == before['calls'] == {'verify':0,'settle':0}
        assert after['transactions'] == before['transactions'] == 0
        assert not any(payment for _,_,_,payment in calls[count:])
    finally:
        if process.poll() is None: stop(process,log)
