"""Run with the pristine fixed-parent SDK, producing actual encrypted journals."""
import hashlib,json,os,sys,urllib.request,urllib.error
from pathlib import Path
from ln_church_agent import structured_response_contract as c
from ln_church_agent.structured_response_client import StructuredResponseClient
from ln_church_agent.structured_response_transport import StructuredResponseTransport,StructuredResponseError
from ln_church_agent.immediate_visit_transport import ImmediateVisitRawResponse

origin,task_id,directory=sys.argv[1:]
lost=False
def exchange(method,path,query,headers,body,timeout):
    if lost and path.endswith('/status'):
        raise StructuredResponseError('TRANSPORT_ERROR',request_bytes_sent=False)
    try:
        response=urllib.request.urlopen(urllib.request.Request(origin+path,method=method,
            headers=dict(headers),data=body if method!='GET' else None),timeout=timeout)
    except urllib.error.HTTPError as error: response=error
    with response:
        value=ImmediateVisitRawResponse(response.status,dict(response.headers),response.read())
    if lost and path.endswith('/completion'):
        raise StructuredResponseError('TRANSPORT_ERROR',request_bytes_sent=True)
    return value
client=StructuredResponseClient(transport=StructuredResponseTransport(exchange=exchange))
result=[]
for index,state in enumerate(['accepted','unknown']):
    lost=state=='unknown'
    op=client.claim(task_id,'legacy-sdk','0x'+str(index+1)*40,'original-'+state,journal_directory=directory)
    task=op.journal.load_task()
    entries=[dict(item_id=i['item_id'],status='unknown',value=None,reason='Synthetic unknown.') for i in task.items if i['required']]
    category=next(i for i in entries if i['item_id']=='category')
    category.update(status='answered',value={'name':'Synthetic category'},reason='')
    report=client.prepare_answer(journal=op.journal,shared_assumptions={'must':'','volume':'','outcome':''},items=entries)
    outcome=client.submit(journal=op.journal,max_post_requests=1)
    assert outcome.state==state
    result.append(dict(state=state,handle=op.journal.handle,report_sha256=report.report_sha256,
        terms_digest=op.claim.terms_digest,execution_id=op.claim.execution_id,submission_id=report.report['submission_id']))
print(json.dumps(dict(sdk_source=c.__file__,operations=result)))
