"""Installed CLI harness: replace only the network exchange with loopback HTTP."""
import sys
import urllib.parse
import httpx

sys.path.insert(0, sys.argv.pop(1))
port = int(sys.argv.pop(1))
from mayim_agent.task_transport import TaskTransport
from mayim_agent.task_v2_transport import TaskV2Transport, TaskV2RawResponse

def send(method, path, params, headers, body):
    with httpx.Client(trust_env=False) as client:
        r = client.request(method, 'http://127.0.0.1:%s%s' % (port, path),
            params=params, headers=headers, content=body)
        return r.status_code, dict(r.headers), r.content

def discovery_exchange(**kw):
    url = urllib.parse.urlsplit(kw['url'])
    assert url.netloc == 'kari.mayim-mayim.com'
    headers = {'x-ln-task-claim-token': kw['claim_token']} if kw['claim_token'] else {}
    return send(kw['method'], url.path, kw['params'], headers, kw['body'])

def scheduled_exchange(method, path, query, headers, body):
    return TaskV2RawResponse(*send(method, path, dict(urllib.parse.parse_qsl(query or '')), headers, body))

original = TaskTransport.__init__
def discovery_init(self, *args, **kw):
    kw.update(_resolver=lambda *a: ['93.184.216.34'], _exchange=discovery_exchange)
    original(self, *args, **kw)
TaskTransport.__init__ = discovery_init
original_v2 = TaskV2Transport.__init__
def scheduled_init(self, *args, **kw):
    kw['exchange'] = scheduled_exchange
    original_v2(self, *args, **kw)
TaskV2Transport.__init__ = scheduled_init

from mayim_agent.cli import main
main()
