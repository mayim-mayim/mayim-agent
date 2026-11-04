"""Pin package import root; redirect only Graph transport and clock."""
import os, runpy, sys
from pathlib import Path
sys.path.insert(0, sys.argv.pop(1))
import httpx, mayim_agent
assert Path(mayim_agent.__file__).resolve().parent.parent == Path(sys.path[0]).resolve()
from mayim_agent.activity_map import ActivityMapClient
port = int(os.environ['SDK_GRAPH_BRIDGE_PORT']); surface = sys.argv.pop(1)
class Loopback(httpx.BaseTransport):
    def __init__(self): self.inner = httpx.HTTPTransport(trust_env=False)
    def handle_request(self, request):
        assert request.url.host == 'kari.mayim-mayim.com'
        request.url = request.url.copy_with(scheme='http',host='127.0.0.1',port=port)
        return self.inner.handle_request(request)
    def close(self): self.inner.close()
original = ActivityMapClient.__init__
def local(self, directory, **kwargs):
    kwargs.update(transport=Loopback(),clock=lambda:float(os.environ['SDK_GRAPH_CLOCK']),sleep=lambda _:None)
    original(self,directory,**kwargs)
ActivityMapClient.__init__ = local
if surface == 'cli':
    from mayim_agent.cli import main
    main()
else:
    runpy.run_module('mayim_agent.integrations.mcp',run_name='__main__')
