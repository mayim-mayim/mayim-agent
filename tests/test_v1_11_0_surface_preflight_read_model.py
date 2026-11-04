import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mayim_agent.client import MagClient, SURFACE_PREFLIGHT_SCHEMA_VERSION


def make_valid_response(known=True):
    return {
        'schema_version': SURFACE_PREFLIGHT_SCHEMA_VERSION,
        'not_a_recommendation': True, 'not_a_verdict': True, 'surface': {'known': known},
        'guardrails': {'final_authority': 'local_runtime',
                       'this_read_model_does_not_execute_payments': True,
                       'this_read_model_does_not_prove_settlement': True},
    }


@pytest.fixture(params=[False, True], ids=['sync', 'async'])
def preflight(request):
    client = MagClient(base_url='https://api.test')
    response = MagicMock(status_code=200)
    target = 'httpx.AsyncClient.get' if request.param else 'requests.get'
    with patch(target, new_callable=AsyncMock if request.param else MagicMock, return_value=response) as transport, \
         patch.object(client, 'execute_request', side_effect=AssertionError('purchase')), \
         patch.object(client, 'execute_request_async', side_effect=AssertionError('purchase')):
        def invoke(**kwargs):
            if request.param:
                return asyncio.run(client.get_surface_preflight_async(**kwargs))
            return client.get_surface_preflight(**kwargs)
        yield invoke, response, transport








def test_internal_surface_key_derivation():
    from mayim_agent.client import _derive_surface_key
    key = _derive_surface_key(target_url='https://api.example.com/endpoint?volatile=ignore', method='GET', rail='x402')
    assert len(key) == 24
