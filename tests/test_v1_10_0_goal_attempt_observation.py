import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mayim_agent.client import MagClient






@pytest.mark.parametrize('async_mode', [False, True])
def test_normal_execution_has_no_observation_or_verification_hook(async_mode):
    client = MagClient(base_url='https://api.test')
    response = MagicMock(status_code=200, content=b'{}', headers={})
    response.json.return_value = {'status': 'success'}
    name = 'httpx.AsyncClient.request' if async_mode else 'requests.request'
    with patch(name, new_callable=AsyncMock if async_mode else MagicMock, return_value=response) as transport:
        if async_mode:
            async def run():
                try:
                    return await client.execute_request_async('POST', '/api/v1/resource', {'test': 'data'})
                finally:
                    await client.aclose()
            asyncio.run(run())
        else:
            client.execute_request('POST', '/api/v1/resource', {'test': 'data'})
    assert transport.call_count == 1
    assert transport.call_args.args[1] == 'https://api.test/api/v1/resource'
