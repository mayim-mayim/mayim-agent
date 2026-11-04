"""Subprocess test entry: redirect only HTTP sockets to the isolated Backend."""
import os
import runpy
import sys

sys.path.insert(0, sys.argv.pop(1))
import httpx
from mayim_agent.activity_map import ActivityMapClient

port=int(sys.argv.pop(1));surface=sys.argv.pop(1)
class Loopback(httpx.BaseTransport):
    def __init__(self):self.inner=httpx.HTTPTransport(trust_env=False)
    def handle_request(self,request):
        if request.url.host!='kari.mayim-mayim.com':raise AssertionError('External HTTP forbidden')
        request.url=request.url.copy_with(scheme='http',host='127.0.0.1',port=port)
        return self.inner.handle_request(request)
    def close(self):self.inner.close()

original=ActivityMapClient.__init__
def local(self,directory,**kwargs):
    kwargs.update(transport=Loopback(),clock=lambda:float(os.environ.get('MAG_SURFACE_CLOCK','1793491200.0')),sleep=lambda n:None)
    original(self,directory,**kwargs)
ActivityMapClient.__init__=local
if surface=='cli':
    from mayim_agent.cli import main
    main()
else:
    name={'execution':'mcp','inspect':'mcp_inspect','responder':'mcp_structured_response'}[surface]
    runpy.run_module('mayim_agent.integrations.'+name,run_name='__main__')
