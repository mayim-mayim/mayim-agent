"""Actual CLI and MCP child processes; socket-only redirection, no live assets."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_mag_activity_map_http import Bridge, pending_acquisition

pytestmark=pytest.mark.skipif(not (os.environ.get('MAG_BACKEND_C3_ROOT') or os.environ.get('MAG_BACKEND_C2_ROOT') or os.environ.get('MAG_BACKEND_C1_ROOT')),reason='Fixed mAG Backend c1/c2/c3 modules required')
ROOT=Path(__file__).resolve().parents[1]
SOURCE=os.environ.get('MAG_INSTALLED_ROOT',str(ROOT))
ENTRY=str(ROOT/'tests/fixtures/mag/surface-entry.py')

@pytest.fixture
def bridge():
    b=Bridge()
    try:yield b
    finally:b.stop()

def child(bridge,surface,*args):return [sys.executable,'-I',ENTRY,SOURCE,str(bridge.port),surface,*args]

def test_cli_grant_entry_removed_and_rejected(bridge,tmp_path):
    prefix = child(bridge,'cli','graph','download')
    help_result = subprocess.run(prefix+['--help'],capture_output=True,text=True,timeout=20,check=True)
    assert '--grant-file' not in help_result.stdout
    args = ['--directory',str(tmp_path/'private'),'--scheme','grant','--asset','USDC']
    result = subprocess.run(prefix+args,capture_output=True,text=True,timeout=20)
    assert result.returncode == 1
    assert json.loads(result.stdout)['error'] == 'GRANT_NOT_SUPPORTED'
    result = subprocess.run(prefix+args+['--grant-file','unused.json'],capture_output=True,text=True,timeout=20)
    assert result.returncode == 2
    assert not (tmp_path/'private').exists()
    assert bridge.command(action='counts')['calls'] == []


def test_execution_mcp_has_no_grant_download_contract(bridge,tmp_path):
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    async def check():
        argv=child(bridge,'execution')
        async with stdio_client(StdioServerParameters(command=argv[0],args=argv[1:])) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                tool=next(t for t in (await session.list_tools()).tools if t.name=='download_activity_map')
                properties=tool.inputSchema['properties']
                assert 'grant_file' not in properties
                assert 'grant' not in json.dumps(properties['scheme'])
                result=await session.call_tool('download_activity_map',dict(directory=str(tmp_path/'private'),scheme='grant',asset='USDC'))
                assert result.isError
    asyncio.run(asyncio.wait_for(check(),30))
    assert not (tmp_path/'private').exists()
    assert bridge.command(action='counts')['calls'] == []


def test_cli_recovery_and_status_real_process(bridge,tmp_path):
    worker,op=pending_acquisition(bridge,tmp_path);bridge.command(action='fault')
    for action in ('resume','status'):
        result=subprocess.run(child(bridge,'cli','graph',action,'--directory',str(worker.directory),'--operation-id',op),
            capture_output=True,text=True,timeout=20,check=True)
        assert json.loads(result.stdout)['state']=='COMPLETE'
    assert bridge.command(action='counts')['settlements']==1

def test_cli_replacement_decline_real_process(bridge,tmp_path):
    worker,op=pending_acquisition(bridge,tmp_path);bridge.command(action='revoke',replacement=True)
    result=subprocess.run(child(bridge,'cli','graph','replacement','--directory',str(worker.directory),'--operation-id',op,'--decision','decline'),
        capture_output=True,text=True,timeout=20,check=True)
    assert json.loads(result.stdout)['state']=='UNAVAILABLE'
    assert bridge.command(action='counts')['settlements']==1


@pytest.mark.parametrize('surface',['cli','execution'])
def test_unconfirmed_complete_corrupt_part_recovery_real_process(bridge,tmp_path,surface):
    from test_mag_sdk_c2_recovery import unconfirmed_complete,damage
    worker,op,saved=unconfirmed_complete(bridge,tmp_path)
    target,expected=damage(worker,op,'data','corrupt');bridge.command(action='fault')
    if surface=='cli':
        result=subprocess.run(child(bridge,'cli','graph','resume','--directory',str(worker.directory),'--operation-id',op),
            capture_output=True,text=True,timeout=20,check=True)
        payload=json.loads(result.stdout)
    else:
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        async def recover():
            argv=child(bridge,'execution')
            async with stdio_client(StdioServerParameters(command=argv[0],args=argv[1:])) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    result=await session.call_tool('resume_activity_map',dict(directory=str(worker.directory),operation_id=op))
                    assert not result.isError
                    return result.structuredContent or json.loads(next(x.text for x in result.content if x.type=='text'))
        payload=asyncio.run(asyncio.wait_for(recover(),30))
    assert payload['state']=='COMPLETE' and target.read_bytes()==expected
    assert payload['expires_at']==saved['purchase']['expires_at']
    assert bridge.command(action='counts')['settlements']==1

def test_execution_mcp_resume_and_keyless_surfaces_real_process(bridge,tmp_path):
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    worker,op=pending_acquisition(bridge,tmp_path);bridge.command(action='fault')
    expected={'download_activity_map','resume_activity_map','activity_map_status','replace_activity_map'}
    async def check():
        for surface in ('inspect','responder','execution'):
            argv=child(bridge,surface)
            params=StdioServerParameters(command=argv[0],args=argv[1:],env={'PATH':os.environ.get('PATH','')})
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize();names={t.name for t in (await session.list_tools()).tools}
                    if surface!='execution':assert not expected & names
                    else:
                        assert expected<=names
                        for name in ('resume_activity_map','activity_map_status'):
                            result=await session.call_tool(name,{'directory':str(worker.directory),'operation_id':op})
                            assert not result.isError
                            payload=result.structuredContent
                            if payload is None:payload=json.loads(next(x.text for x in result.content if x.type=='text'))
                            assert payload['state']=='COMPLETE'
    asyncio.run(asyncio.wait_for(check(),40))
    assert bridge.command(action='counts')['settlements']==1


def initial_arguments(bridge,tmp_path,scheme):
    from mayim_agent import activity_map_contract as c
    if scheme=='x402':
        from test_v1_16_2_payment_boundary import _exact_payload,_encode_requirement
        challenge=_exact_payload({'amount':'10000'},{'resource':{'url':c.ORIGIN+c.PREFIX+'/purchases'}})
        bridge.command(action='challenge',body=challenge,headers={'PAYMENT-REQUIRED':_encode_requirement(challenge)})
        return {'AGENT_PRIVATE_KEY':'0x'+'1'*64},{}


@pytest.mark.parametrize('surface',['cli','execution'])
@pytest.mark.parametrize('scheme',['x402'])
def test_initial_acquisition_real_process(bridge,tmp_path,surface,scheme):
    credentials,extra=initial_arguments(bridge,tmp_path,scheme)
    directory=str(tmp_path/'private')
    env=dict(os.environ,**credentials)
    env.pop('LN_API_KEY',None);env.pop('LN_API_URL',None)
    if surface=='cli':
        args=['graph','download','--directory',directory,'--scheme',scheme,'--asset','USDC']
        for key,value in extra.items():args+=['--'+key.replace('_','-'),value]
        result=subprocess.run(child(bridge,'cli',*args),env=env,capture_output=True,text=True,timeout=20,check=True)
        payload=json.loads(result.stdout)
    else:
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        async def acquire():
            argv=child(bridge,'execution');params=StdioServerParameters(command=argv[0],args=argv[1:],env=env)
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    result=await session.call_tool('download_activity_map',dict(directory=directory,scheme=scheme,asset='USDC',**extra))
                    assert not result.isError,str(result)
                    return result.structuredContent or json.loads(next(x.text for x in result.content if x.type=='text'))
        payload=asyncio.run(asyncio.wait_for(acquire(),30))
    assert payload['state']=='COMPLETE'
    assert bridge.command(action='counts')['settlements']==1
    assert 'grant_token' not in json.dumps(payload) and 'private_key' not in json.dumps(payload)
