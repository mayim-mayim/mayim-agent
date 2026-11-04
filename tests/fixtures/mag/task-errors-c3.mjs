// Actual fixed c3 TaskGet dispatch + family owners. Physical store is a double.
import {pathToFileURL} from 'node:url';
const root=process.env.MAG_BACKEND_C3_ROOT,imp=p=>import(pathToFileURL(root+'/'+p));
globalThis.fetch=()=>{throw Error('External network forbidden');};
const {fixture}=await imp('tests/post-v1814/task-discovery-backend-c1/fixture.mjs');
const {REGISTRY}=await imp('backend/shared/task-discovery/registry.mjs');
const {createTaskGetHandler}=await imp('ai_agent/sandbox-runtime-bridge/Lambda_TaskGet.js');
const {createChoiceLifecycle}=await imp('backend/shared/task-venue/endpoint-choice-reason-lifecycle.mjs');
const {taskProjection}=await imp('backend/shared/task-venue/endpoint-choice-reason-core.mjs');
const {createStructuredExecution}=await imp('backend/shared/task-venue/structured-response-execution.mjs');
const {createStructuredList}=await imp('backend/shared/task-venue/structured-response-read-runtime.mjs');
const rows=REGISTRY.filter(r=>['2.4.0','3.2.0'].includes(r.version)),f=fixture(),out=[];
f.legacy.choiceDetail=async id=>taskProjection(await createChoiceLifecycle({store:f.choice}).expire(id),f.clock());
f.legacy.structuredDetail=createStructuredExecution({store:f.structured}).detail;
f.legacy.structuredList=createStructuredList({store:f.structured,detail:async()=>{},cursorCodec:async()=>{}});
const outage=createTaskGetHandler({serviceFactory:async()=>{throw Error('Synthetic provider unavailable');}});
for(const r of rows){
 const selector={task_type:r.type,task_schema_version:r.schema};
 for(const extra of [{unknown:'bad'},{limit:'0'}])out.push({family:r.family,query:{...selector,...extra},...(await f.request({...selector,...extra}))});
 const query=r.family==='paid'?{task_schema_version:r.schema}:selector;
 out.push({family:r.family,query,task_id:'task_missing',...(await f.request(query,undefined,{pathParameters:{task_id:'task_missing'}}))});
 const result=await outage({headers:{'x-ln-access-origin-auth':'td-c1-isolated'},queryStringParameters:selector,requestContext:{requestId:'sdk-c2'}});
 out.push({family:r.family,query:selector,status:result.statusCode,headers:result.headers,value:JSON.parse(result.body)});
 for(const schema of ['attacker.unrecognized.schema',rows.find(x=>x.family!==r.family).schema]){
  const result=await f.request({task_type:r.type,task_schema_version:schema});
  if(result.status!==400||result.value.schema_version===schema)throw Error('Selector leaked');
 }
}
const old=REGISTRY.find(r=>r.family==='immediate'&&r.version==='3.1.0');
const legacy=await f.request({task_type:old.type,task_schema_version:old.schema,unknown:'bad'});
if(legacy.status!==400||legacy.value.schema_version!=='ln_church.task_error.immediate_visit.v3')throw Error('Legacy error changed');
process.stdout.write('SDK_C3_TASK_ERRORS '+JSON.stringify(out)+'\n');
