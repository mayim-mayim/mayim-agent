"""A11: explicit rename without changing credentials or saved Task identity."""
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import mayim_agent
from mayim_agent import MagClient, Payment402Client
from mayim_agent.evaluators import RemoteTrustEvaluator, RemoteOutcomeMatcher


@pytest.mark.parametrize('name', [
    'get_benchmark_overview', 'compare_trial_performance',
    'request_fast_pass_aggregate', 'submit_monzen_trace', 'get_site_metrics',
    'download_monzen_graph', 'run_l402_sandbox_harness',
    'run_mpp_charge_sandbox_harness', 'run_corpus_replay',
    'run_x402_evm_exact_sandbox_diagnostic', 'run_x402_svm_exact_sandbox_diagnostic',
    'get_surface_preflight', 'submit_goal_attempt_observation',
    'get_goal_attempt_summary', 'get_goal_surface_candidates',
    'register_domain_observation_slot', 'get_domain_observation_request',
    'get_domain_observation_read_model', 'claim_domain_observation_targets',
    'submit_domain_observation_result', 'create_domain_sponsor_challenge',
    'verify_domain_sponsor', 'register_verified_domain_track',
    'get_verified_domain_track_status', 'get_domain_verified_track',
    'submit_sandbox_interop_report', 'get_sandbox_evidence_logs',
])
def test_retired_service_has_no_sync_or_async_wrapper(name):
    assert not hasattr(MagClient, name)
    assert not hasattr(MagClient, name + '_async')


def test_identity_and_generic_payment_runtime_are_preserved():
    # Public synthetic development key; no signing or network activity.
    client = MagClient(private_key='0x' + '0' * 63 + '1')
    assert isinstance(client, Payment402Client)
    assert client.agent_id == '0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf'
    for name in ('draw_omikuji','submit_confession','offer_hono',
                 'issue_identity','resolve_identity','submit_external_observation',
                 'get_external_observations','ensure_reporter_verification'):
        assert callable(getattr(client, name))
        assert callable(getattr(client, name+'_async'))
    assert not hasattr(mayim_agent, 'LnChurchClient')


@pytest.mark.parametrize('cls,path', [
    (RemoteTrustEvaluator, 'trust/evaluate'),
    (RemoteOutcomeMatcher, 'outcome/verify'),
])
def test_retired_evaluator_requires_explicit_configuration(cls,path,monkeypatch):
    monkeypatch.setattr('requests.post', lambda *a,**k: pytest.fail('unexpected HTTP'))
    with pytest.raises(ValueError, match='Explicitly configure'):
        cls('https://kari.mayim-mayim.com/api/agent/monzen/'+path)
    custom = cls('https://evaluator.example/api/'+path)
    assert custom.endpoint_url.startswith('https://evaluator.example/')
    custom.endpoint_url='https://kari.mayim-mayim.com/api/agent/monzen/'+path
    with pytest.raises(ValueError, match='retired'):
        custom(None,None) if cls is RemoteTrustEvaluator else custom(None,None,None)


def test_keyless_mcp_exposes_no_execute_or_submission_tools():
    from mayim_agent.integrations.mcp_inspect import mcp
    names={tool.name for tool in asyncio.run(mcp.list_tools())}
    assert names == {'inspect_paid_surface','explain_recommended_action'}


def test_execution_mcp_removes_retired_services_but_keeps_rituals():
    from mayim_agent.integrations.mcp import mcp
    names={tool.name for tool in asyncio.run(mcp.list_tools())}
    assert {'execute_paid_entropy_oracle','normalize_diagnostic_log'} <= names
    assert not names & {'analyze_trial_performance','compare_trial_performance',
        'report_discovered_402_paywall','get_discovered_paywall_metrics',
        'download_resonance_graph_dataset','download_monzen_graph_data'}


def test_cli_has_new_name_and_no_retired_domain_commands():
    run=subprocess.run([sys.executable,'-m','mayim_agent.cli','--help'],capture_output=True,text=True,check=True)
    assert 'mayim-agent' in run.stdout
    assert 'observe-domain' not in run.stdout and 'observatory' not in run.stdout


def test_old_key_environment_is_not_a_fallback(tmp_path,monkeypatch):
    from mayim_agent._structured_response_crypto import _key
    from mayim_agent.task_journal import JournalError
    monkeypatch.delenv('MAG_STRUCTURED_RESPONSE_KEY_FILE', raising=False)
    monkeypatch.setenv('LN_CHURCH_STRUCTURED_RESPONSE_KEY_FILE', str(tmp_path/'old.key'))
    with pytest.raises(JournalError) as error:
        _key(tmp_path/'saved.json')
    assert error.value.code == 'JOURNAL_KEY_REQUIRED'


def test_original_11817_encrypted_journal_moves_without_rewriting(tmp_path,monkeypatch):
    """Run actual parent producer, then reopen the moved bytes with 2.0."""
    parent=os.environ.get('MAG_TEST_PARENT_SOURCE')
    if not parent:
        pytest.skip('Set MAG_TEST_PARENT_SOURCE to the exact 18006cd parent checkout')
    old=tmp_path/'ln-church-agent';old.mkdir(mode=0o700)
    keys=old/'keys';keys.mkdir(mode=0o700)
    claims=old/'claims';claims.mkdir(mode=0o700)
    key=keys/'original.key';key.write_bytes(os.urandom(32));key.chmod(0o600)
    script='''import json,sys
from pathlib import Path
from test_v1_18_14_s3_encrypted_journal import prepared,snapshot
j,r=prepared(Path(sys.argv[1]))
print(json.dumps(dict(handle=j.handle,snapshot=snapshot(j))))
'''
    env=dict(os.environ, PYTHONPATH=parent+os.pathsep+str(Path(parent)/'tests'),
             LN_CHURCH_STRUCTURED_RESPONSE_KEY_FILE=str(key))
    run=subprocess.run([sys.executable,'-c',script,str(claims)],env=env,
        cwd=parent,capture_output=True,text=True,check=True)
    original=json.loads(run.stdout)
    before={str(p.relative_to(old)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in old.rglob('*') if p.is_file()}
    new=tmp_path/'mayim-agent';old.rename(new)
    monkeypatch.setenv('MAG_STRUCTURED_RESPONSE_KEY_FILE', str(new/'keys/original.key'))
    from mayim_agent.structured_response_journal import StructuredResponseJournal
    from mayim_agent import structured_response_contract as c
    j=StructuredResponseJournal.open(new/'claims',original['handle'])
    snapshot=dict(logical_sha256=c.digest(j._load()),
        token_sha256=hashlib.sha256(j.load_claim()._claim_token_value().encode()).hexdigest(),
        report_sha256=j.load_report().report_sha256,
        claim_request_sha256=hashlib.sha256(j.claim_request()[2]).hexdigest())
    assert snapshot == original['snapshot']
    assert {str(p.relative_to(new)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in new.rglob('*') if p.is_file()} == before
