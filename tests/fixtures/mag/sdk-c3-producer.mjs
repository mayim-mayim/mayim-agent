// Isolated SDK bridge over the exact c4 Gateway and Task owners. The fixed
// Backend test harness supplies only in-memory I/O, ephemeral keys and RPC.
import {readFileSync} from 'node:fs';
import {registerHooks} from 'node:module';
import {pathToFileURL} from 'node:url';
import {createInterface} from 'node:readline';
import http from 'node:http';
const root=process.env.MAG_BACKEND_C4_ROOT;
if(!root)throw Error('Fixed c4 source required');
const imp=p=>import(pathToFileURL(root+'/'+p));
const harness=pathToFileURL(root+'/tests/mag-backend-c4/gateway.test.mjs').href;
const hook=registerHooks({load(url,context,next){
 if(url===harness){const original=readFileSync(new URL(url),'utf8');
  return {format:'module',shortCircuit:true,source:original.replace("import test from 'node:test';",'const test=()=>{};')+
   `\nexport async function sdkInit(){Object.assign(console,savedConsole);io=await createExternalIO();return io;}\nexport {request as sdkRequest, signed as sdkSigned};`};}
 if(url==='sdk-c3:agent-utils')return {format:'module',shortCircuit:true,source:'export const getCorsHeaders=()=>({});export const buildResponse=(statusCode,body,headers)=>({statusCode,headers,body:JSON.stringify(body)});'};
 return next(url,context);
},resolve(specifier,context,next){if(specifier==='/opt/nodejs/agent-utils.mjs')return {url:'sdk-c3:agent-utils',shortCircuit:true};return next(specifier,context);}});
const {sdkInit,sdkRequest,sdkSigned}=await import(harness);
Object.assign(process.env,{MAG_ACTIVITY_MAP_EPOCH:'isolated-c4',MAG_ACTIVITY_EVENT_TABLE:'Events',MAG_ACTIVITY_CONTROL_TABLE:'Control',
 TABLE_NAME:'Observations',DOMAIN_OBSERVATIONS_TABLE:'Observations',EVENTLOG_TABLE:'Events',TASK_ECONOMIC_TERMS_NEW_SALES_ENABLED:'true'});
const io=await sdkInit();
const {GetCommand,TransactWriteCommand,QueryCommand,UpdateCommand,BatchGetCommand}=await imp('node_modules/@aws-sdk/lib-dynamodb/dist-cjs/index.js');
const sendDB=io.database.send.bind(io.database);
io.database.send=async command=>{
 if(command.constructor.name==='BatchGetCommand')return {Responses:Object.fromEntries(Object.entries(command.input.RequestItems).map(([table,q])=>[table,q.Keys.map(k=>io.database.get(table,k)).filter(Boolean)])),UnprocessedKeys:{}};
 return sendDB(command);
};
const {createTaskClaimService}=await imp('ai_agent/sandbox-runtime-bridge/Lambda_TaskGetClaim.js');
const {createTaskGetService}=await imp('ai_agent/sandbox-runtime-bridge/Lambda_TaskGet.js');
const {createTaskCompletionService,createV18CompletionHandler}=await imp('ai_agent/sandbox-runtime-bridge/Lambda_TaskCompletionReport.js');
const {submissionStatusV2}=await imp('backend/shared/task-venue/scheduled-http-get-batch-adapters.mjs');
const {sha256Jcs}=await imp('backend/shared/task-venue/rfc8785-jcs.mjs');
const {projectSource,candidateFromRow}=await imp('backend/shared/task-discovery/projection.mjs');
const {handler:observe}=await imp('ai_agent/sandbox-runtime-bridge/Lambda_OpenClawResults.js');
const deps={docClient:io.database,GetCommand,TransactWriteCommand,QueryCommand,UpdateCommand,BatchGetCommand,tableName:'Tasks',executionTableName:'Executions',observationTableName:'Observations',eventTableName:'Events',campaignReportTableName:'Reports'};
const claims=createTaskClaimService({...deps,addressCanonicalizer:async x=>x,
 manifestUrlSigner:async()=> 'https://tasks-release.mayim-mayim.com/synthetic?signature=synthetic-unfunded'});
const reads=createTaskGetService(deps),completion=createTaskCompletionService(deps);
const receipts=new Map();let scheduledNow=Date.now();
const scheduledService={
 getExecutionByToken:async hash=>[...io.database.rows.values()].find(v=>v.claim_token_sha256===hash),
 getExistingReceipt:async(id,sub)=>receipts.get(id+'/'+sub),
 getOffer:async id=>io.database.get('Tasks',{PK:'TASK#'+id,SK:'TASK'}),
 readPrivateManifest:async()=>Buffer.from('{"method":"GET","schema_version":"ln_church.http_get_batch_manifest.v1","urls":["https://example.com/"]}'),
 commitReceipt:async({offer,execution,report,reportId,completionId,receipt,projection,requestBytesSha256,acceptedAt})=>{
  const e={...execution,state:'SUBMITTED',report_id:reportId,completion_id:completionId,submission_id:report.submission_id,
   report_sha256:sha256Jcs(report),accepted_at:new Date(acceptedAt).toISOString()};
  io.database.put('Executions',e);
  receipts.set(offer.task_id+'/'+report.submission_id,{...receipt,execution_id:e.execution_id,receipt,
   request_bytes_sha256:requestBytesSha256,accepted_at:e.accepted_at});
 }
};
const scheduledCompletion=createV18CompletionHandler({service:scheduledService,clock:()=>scheduledNow});
const initial=JSON.parse(readFileSync(root+'/backend/shared/task-venue/economic-initial-offers.json'));
const row=initial.rows.find(x=>x.PK==='TASK_ECONOMIC_TERMS#scheduled');io.database.seed('Tasks',{...row,enabled:true});
const json=(status,body,headers={})=>({status,body,headers});
let httpServer;
const send=async q=>{
 if(q.control==='http'){
  httpServer=http.createServer(async(req,res)=>{try{
   const chunks=[];for await(const chunk of req)chunks.push(chunk);
   const url=new URL(req.url,'http://127.0.0.1');
   const r=await send({method:req.method,path:url.pathname,query:Object.fromEntries(url.searchParams),headers:req.headers,body:Buffer.concat(chunks).toString()});
   res.writeHead(r.status,{'content-type':'application/json',...r.headers});res.end(JSON.stringify(r.body));
  }catch{res.writeHead(500,{'content-type':'application/json'});res.end('{"error":"synthetic_bridge_failure"}');}});
  await new Promise(resolve=>httpServer.listen(0,'127.0.0.1',resolve));return {port:httpServer.address().port};
 }
 if(q.control==='info')return {payer:io.payer,control_key:io.controlKey,economics:row.offers[0],counts:io.counts};
 if(q.control==='read-signature')return {signature:await io.account.signTypedData(q.typed_data)};
 if(q.control==='discovery'){
  const now=new Date().toISOString(),tasks=[...io.database.rows.values()].filter(x=>x.SK==='TASK').map(projectSource).filter(Boolean).map(x=>candidateFromRow({...x,projection_updated_at:now}));
  return {schema_version:'ln_church.task_discovery_page.v1',selection:'recruiting_candidates',coverage:'all_supported_families',checked_at:now,page_scope:'initial',tasks,next_cursor:null,
   empty_reason:tasks.length?null:'no_candidates_observed_in_range',claim_authority:'task_claim_endpoint'};
 }
 if(q.control==='epoch'){if(q.epoch)process.env.MAG_ACTIVITY_MAP_EPOCH=q.epoch;else delete process.env.MAG_ACTIVITY_MAP_EPOCH;return {ok:true};}
 if(q.control==='seed-registration'){if(q.legacy&&q.profile==='scheduled'){delete process.env.TASK_ECONOMIC_TERMS_NEW_SALES_ENABLED;io.database.put('Tasks',{...row,enabled:false});}const s=await sdkSigned(q.profile,{legacy:!!q.legacy});const r=await sdkRequest('/api/bazaar/task-offers',{method:'POST',headers:s.headers,body:s.body});return {...r,saved:{body:s.body.toString(),headers:s.headers,reference:s.reference}};}
 if(q.control==='terms')return io.database.get('Tasks',{PK:'TASK#'+q.task_id,SK:'TASK'}).terms_snapshot;
 if(q.control==='scheduled-time'){
  const task=io.database.get('Tasks',{PK:'TASK#'+q.task_id,SK:'TASK'});
  scheduledNow=Date.parse(task.scheduled_at)+1000;
  io.database.put('Tasks',{...task,status:'RUNNING',report_intake_fence:'OPEN'});
  return {now:new Date(scheduledNow).toISOString(),manifest_sha256:task.manifest_sha256};
 }
 const headers=Object.fromEntries(Object.entries(q.headers??{}).map(([k,v])=>[k.toLowerCase(),v]));
 if(q.path.startsWith('/api/bazaar/')||q.path.includes('task-offer-registration')){
  const r=await sdkRequest(q.path,{method:q.method,headers,body:q.body});return {...r,headers:Object.fromEntries(r.headers)};
 }
 const p=q.path.split('/'),task_id=p[4],body=q.body?JSON.parse(q.body):{},query=q.query??{};
 if(q.method==='GET'&&q.path==='/api/agent/tasks')return json(200,query.task_schema_version==='ln_church.agent_task.v2_2'?await reads.v18List({definitionVersion:'1.2.0'}):await reads.list({definitionVersion:query.task_schema_version==='ln_church.agent_task.v1_1'?'1.1.0':'1.0.0'}));
 const scheduled=io.database.get('Tasks',{PK:'TASK#'+task_id,SK:'TASK'})?.task_type==='scheduled_http_get_batch.v1';
 if(q.method==='GET'&&p.length===5)return json(200,scheduled?await reads.v18Detail(task_id):await reads.detail(task_id));
 if(p[5]==='claim')return json(200,await claims[scheduled?'v18Claim':'claim']({task_id,...body}));
 if(p[5]==='domain-observations'){const r=await observe({pathParameters:{task_id},httpMethod:'POST',headers,body:q.body});return json(r.statusCode,JSON.parse(r.body),r.headers);}
 if(p[5]==='completion'){
  if(scheduled){const r=await scheduledCompletion({pathParameters:{task_id},headers,body:q.body});return json(r.statusCode,JSON.parse(r.body),r.headers);}
  const r=await completion.report({task_id,claim_token:headers['x-ln-task-claim-token'],...body});return json(r.http_status,r.body);
 }
 if(p[5]==='submissions'){
  if(scheduled)return json(200,submissionStatusV2({task:await scheduledService.getOffer(task_id),receipt:receipts.get(task_id+'/'+p[6])}));
  return json(200,await completion.status(task_id,p[6]));
 }
 throw Error('Unhandled SDK bridge route');
};
// No payload-bearing console logs cross the test bridge. Responses remain
// private subprocess pipes and are not persisted by the test harness.
console.log=console.info=console.error=()=>{};
for await(const line of createInterface({input:process.stdin})){
 try{process.stdout.write(JSON.stringify(await send(JSON.parse(line)))+'\n');}
 catch(e){process.stdout.write(JSON.stringify({bridge_error:e.name,code:e.code??null,message:e.message})+'\n');}
}
httpServer?.close();
