// Fixed c4 execution adapter, derived from the retained c2 adapter.
// Actual handlers/services and SQLite; external provider/finality/Jev/witness are doubles.
import {createRequire,registerHooks} from 'node:module';
import {pathToFileURL} from 'node:url';
import http from 'node:http';
const root=process.env.HONDO_ROOT;
const imp=p=>import(pathToFileURL(root+'/'+p).href);
const require=createRequire(root+'/package.json');
const viem=require('viem');
const payTo='0x'+'2'.repeat(40),tx='0x'+'a'.repeat(64);
const {PAYMENT_SURFACE_DISCOVERY_PROFILE}=await imp('backend/shared/task-venue/approved-task-offering-profiles.mjs');
console.log=()=>{}; console.error=()=>{}; console.warn=()=>{}; console.info=()=>{}; console.debug=()=>{};
globalThis.fetch=()=>{throw Error('External network prohibited in SDK integration');};
async function loadGateway(){
    globalThis.__v183GatewayViem=viem;
    const sources={
        'hono':`export class Hono {use(){return this}get(){return this}post(){return this}all(){return this}on(){return this}}`,
        'hono/aws-lambda':'export const handle=app=>app;',
        '@x402/hono':`export const paymentMiddleware=()=>async(c,next)=>next();export class x402ResourceServer {register(){return this}registerExtension(){return this}onAfterVerify(){return this}onBeforeSettle(){return this}onAfterSettle(){return this}onSettleFailure(){return this}}`,
        '@x402/evm/exact/server':'export class ExactEvmScheme {}',
        '@x402/svm/exact/server':'export class ExactSvmScheme {}',
        '@x402/extensions/bazaar':'export const bazaarResourceServerExtension={};export const declareDiscoveryExtension=v=>v;export const validateDiscoveryExtension=()=>({valid:true});',
        'viem':['recoverTypedDataAddress','parseAbi','decodeEventLog','encodeFunctionData','hashTypedData','createPublicClient','http','getAddress'].map(n=>`export const ${n}=(...args)=>globalThis.__v183GatewayViem.${n}(...args);`).join('\n'),
        '@aws-sdk/client-dynamodb':'export class DynamoDBClient {}',
        '@aws-sdk/lib-dynamodb':`export const DynamoDBDocumentClient={from(){return {send(){throw new Error('Unexpected AWS default I/O')}}}};
            ${['GetCommand','UpdateCommand','TransactWriteCommand','QueryCommand'].map(n=>`export class ${n} {constructor(input){this.input=input}}`).join('\n')}`,
        '@aws-sdk/client-lambda':'export class InvokeCommand {} export class LambdaClient {send(){throw new Error("Unexpected Lambda invoke")}}',
        '@aws-sdk/client-s3':'export class S3Client {}',
        '@x402/core/server':'export class HTTPFacilitatorClient {verify(){throw new Error("Unexpected default provider")}settle(){throw new Error("Unexpected default provider")}}',
        '@coinbase/x402':'export const facilitator={url:"https://invalid.example/",createAuthHeaders(){throw new Error("Unexpected auth operation")}};',
    };
    const hooks=registerHooks({resolve(specifier,context,next){return specifier in sources?{url:`v183gateway:${specifier}`,shortCircuit:true}:next(specifier,context);},
        load(url,context,next){return url.startsWith('v183gateway:')?{format:'module',shortCircuit:true,source:sources[url.slice(12)]}:next(url,context);}});
    const env={V17_TASK_DEFINITION_DIGEST:PAYMENT_SURFACE_DISCOVERY_PROFILE.definitionDigest,
        V17_TASK_MANIFEST_SHA256:'f'.repeat(64),V17_TASK_MANIFEST_URL:`https://kari.mayim-mayim.com/agent-task-specs/payment_surface_discovery.v1/1.0.0/${PAYMENT_SURFACE_DISCOVERY_PROFILE.definitionDigest}/manifest.json`,
        TASK_OFFER_REGISTRATION_PAY_TO:payTo};
    const previous=Object.fromEntries(Object.keys(env).map(key=>[key,process.env[key]]));Object.assign(process.env,env);
    try{return await imp('backend/Lambda/gishiki/Lambda_HonoGateway.js');}finally{hooks.deregister();for(const [key,value]of Object.entries(previous)){if(value===undefined)delete process.env[key];else process.env[key]=value;}}
}

const gateway=await loadGateway();
const {memoryImmediateStore}=await imp('tests/v183-execution-fixture.mjs');
const {choiceStore,key}=await imp('backend/shared/task-venue/endpoint-choice-reason-store.mjs');
const {createChoiceRegistrationService}=await imp('backend/shared/task-venue/endpoint-choice-reason-registration.mjs');
const {createImmediateVisitRegistrationService}=await imp('backend/shared/task-venue/immediate-visit-registration.mjs');
const {withRegistrationTiming}=await imp('backend/shared/task-venue/registration-timing.mjs');
const {finality}=await imp('tests/fixtures/v189/registration-evidence.mjs');
const {createRegistrationReadHandler,createRegistrationReadLookup,createRegistrationReadSignatureVerifier}=await imp('backend/shared/task-venue/registration-read-recovery.mjs');
const {createChoiceExecutionService}=await imp('backend/shared/task-venue/endpoint-choice-reason-execution.mjs');
const {createChoiceResults}=await imp('backend/shared/task-venue/endpoint-choice-reason-results.mjs');
const {createChoiceOwnerReadHandler}=await imp('backend/shared/task-venue/endpoint-choice-reason-owner-read.mjs');
const {createChoiceEvaluationService}=await imp('backend/shared/task-venue/endpoint-choice-reason-evaluation.mjs');
const {choiceHttp}=await imp('backend/shared/task-venue/endpoint-choice-reason-runtime.mjs');
let now=Date.parse('2026-10-20T00:00:00Z'),confirmed=true,settles=0,verifies=0,reads=0;
Date.now=()=>now;
const {persistentStore}=await imp('tests/template-survey-sharing/backend/persistent-fixture.mjs');
const persistence=persistentStore(),physical=persistence.physical;
process.env.SURVEY_SHARING_V1816_NEW_SALES_ENABLED='true';
physical.seed({PK:'SURVEY_SHARING_SALES#V1816',SK:'META',version:1,enabled:true},'tasks');
physical.get=(id,SK='META')=>physical.getKey({PK:`IMMEDIATE_OFFER#${id}`,SK});
physical.queryOfferPage=async(id,{prefix='',limit=100,cursor}={})=>{const rows=[...physical.rows.values()].filter(v=>v.PK===`IMMEDIATE_OFFER#${id}`&&v.SK.startsWith(prefix)&&(!cursor||v.SK>cursor.SK)).sort((a,b)=>a.SK.localeCompare(b.SK));return {items:rows.slice(0,limit),cursor:rows.length>limit?{PK:rows[limit-1].PK,SK:rows[limit-1].SK}:null};};
const store=choiceStore(physical,async(taskId,{prefix='',cursor,limit=100}={})=>{
 const rows=[...physical.rows.entries()].filter(([k,v])=>k.startsWith('reports|')&&v.PK===key(taskId).PK&&v.SK.startsWith(prefix)&&(!cursor||v.SK>cursor.SK)).map(x=>x[1]).sort((a,b)=>a.SK.localeCompare(b.SK));
 const items=rows.slice(0,limit);return {items:structuredClone(items),cursor:rows.length>limit?{PK:items.at(-1).PK,SK:items.at(-1).SK}:null};});
const chainReadback=async i=>confirmed?finality(i,tx):{};
const choice=createChoiceRegistrationService({store,clock:()=>now,chainReadback});
const immediate=createImmediateVisitRegistrationService({store:physical,clock:()=>now,chainReadback});
const providers={verify:async p=>{verifies++;return {isValid:true,payer:p.payload.authorization.from};},
 settle:async p=>{settles++;return {success:true,network:'eip155:8453',transaction:tx,payer:p.payload.authorization.from};}};
const options=service=>({serviceFactory:async()=>service,payTo:()=>payTo,reconcile:id=>service.reconcile(id),gate:async()=>{},assertFacilitatorReady:async()=>{},providerClient:providers});
const handlers={choice:gateway.createChoiceRegistrationHandler(options(choice)),immediate:gateway.createImmediateVisitRegistrationHandler(options(immediate))};
process.env.IMMEDIATE_VISIT_V2_REGISTRATION_ENABLED='true';
const keys=async()=>({activeKeyId:'fixture',keys:new Map([['fixture',Buffer.alloc(32,7)]])});
let contractCode='0x',contractValid=true,rpcFails=false;
const verifier=createRegistrationReadSignatureVerifier({viem,client:async()=>({getChainId:async()=>8453,getCode:async()=>{if(rpcFails)throw Error('fixture');return contractCode;},readContract:async()=>contractValid?'0x1626ba7e':'0xffffffff'})});
const read=createRegistrationReadHandler({keys,verifySignature:verifier,lookup:createRegistrationReadLookup({getKey:i=>{reads++;return physical.getKey({...i,table:'tasks'});}}),clock:()=>now});
const {createImmediateVisitExecutionService,immediateVisitErrorResponse}=await imp('backend/shared/task-venue/immediate-visit-execution.mjs');
const {immediateVisitTaskProjection}=await imp('backend/shared/task-venue/immediate-visit-core.mjs');
const immediateExecution=createImmediateVisitExecutionService({store:physical,clock:()=>now,checksumAddress:viem.getAddress});
const execution=createChoiceExecutionService({store,clock:()=>now,checksumAddress:viem.getAddress});
const results=createChoiceResults({store,lifecycle:execution,clock:()=>now});
const ownerRead=createChoiceOwnerReadHandler({keys,verifySignature:verifier,results,clock:()=>now});
let evaluationQ='0';
const evaluation=createChoiceEvaluationService({store,clock:()=>now,adapter:async()=>({q:evaluationQ,returned_model:'jev-1.13.0',usage:{}})});
const runtime={store,execution,results,ownerRead,evaluation};
const {createPaidTrialRegistrationService,paidRegistrationChallenge}=await imp('backend/shared/task-venue/paid-service-trial-registration.mjs');
const {createPaidSampleService,createSampleTokens,sampleKey}=await imp('backend/shared/task-venue/paid-service-trial-sample.mjs');
const {prepareRequest,purchaseBindingDigest}=await imp('backend/shared/task-venue/paid-service-trial-request.mjs');
const {createPaidExecutionService,createPaidExecutionHandler}=await imp('backend/shared/task-venue/paid-service-trial-execution.mjs');
const {createPaidEvaluationService}=await imp('backend/shared/task-venue/paid-service-trial-evaluation.mjs');
const {taskProjection:paidProjection}=await imp('backend/shared/task-venue/paid-service-trial-core.mjs');
const {sha256Jcs}=await imp('backend/shared/task-venue/rfc8785-jcs.mjs');
const asset='0x833589fcd6edb6e08f4c7c32d4f71b54bda02913',address=n=>'0x'+String(n).padStart(40,'0');
const paidStore={getKey:k=>physical.getKey(k),transact:ops=>physical.transact(ops),get:(id,SK='META')=>physical.getKey({PK:`PAID_SERVICE_TRIAL_OFFER#${id}`,SK})};
const paidExecution=createPaidExecutionService({store:paidStore,clock:()=>now}),paidEvaluation=createPaidEvaluationService({store:paidStore,clock:()=>now});
const purchaseRequest=prepareRequest({method:'GET',url:'https://seller.example/data',body:null}),purchase_terms={x402_version:2,authorization_method:'EIP-3009',requirements:{scheme:'exact',network:'eip155:8453',asset,amount:'10000',payTo:address(5),maxTimeoutSeconds:300,extra:{name:'USD Coin',version:'2'}}};
const purchaseTuple={request:purchaseRequest,request_digest:sha256Jcs(purchaseRequest),purchase_terms,purchase_terms_digest:purchaseBindingDigest(purchaseRequest,purchase_terms,3)};
const samples=createPaidSampleService({store:paidStore,clock:()=>now,tokens:createSampleTokens(async()=>({activeKeyId:'test',keys:new Map([['test',Buffer.alloc(32,9)]])})),verifySignature:async()=>true,fetcher:async()=>({status:402,headers:{'payment-required':Buffer.from(JSON.stringify({x402Version:2,resource:{url:purchaseRequest.url},accepts:[purchase_terms.requirements]})).toString('base64')},body:Buffer.alloc(0)})});
const paidRegistration=createPaidTrialRegistrationService({store:paidStore,samples,clock:()=>now,chainReadback:async i=>finality(i)});
let paidSample=null;
const purchase=()=>({network:'eip155:8453',asset,payer:address(3),payTo:address(5),authorization_nonce:'0x'+'b'.repeat(64),amount:'10000',validAfter:String(Math.floor(now/1000)-1),validBefore:String(Math.floor(now/1000)+300)});
function event(x){return {rawPath:x.path,rawQueryString:x.query??'',headers:x.headers,httpMethod:x.method,body:x.body,requestContext:{http:{method:x.method},requestId:'sdk-isolated'}};}
async function run(x){
 now=Math.max(now,Date.now());
 if(x.control==='paid-seed'){
  if(!paidSample){const op=await samples.import({schema_version:'ln_church.paid_service_trial_sample_import_request.v3',import_request_id:'22222222-2222-4222-8222-222222222222',...purchaseTuple,transaction_hash:tx});await samples.complete(await paidStore.getKey(sampleKey(op.body.operation_ref)),{state:'VERIFIED',purchase:purchase(),transaction_hash:tx},now);const sample=await samples.result(await paidStore.getKey(sampleKey(op.body.operation_ref)));paidSample={sample_operation_ref:op.body.operation_ref,sample_attachment_token:sample.sample_attachment_token};}
  const request={schema_version:'ln_church.task_offer_create_request.paid_service_trial.v3_1',task_type:'paid_service_trial.v3',task_definition_version:'3.1.0',economic_terms:x.terms,plan_id:x.terms.plan_id,repeat_policy:'ALLOW_REPEAT',...paidSample,...purchaseTuple};
  if(x.version==='3.2.0')Object.assign(request,{schema_version:'ln_church.task_offer_create_request.paid_service_trial.v3_2',task_definition_version:'3.2.0',activity_map_policy_id:'mag.activity-map.public.v1',activity_map_epoch:process.env.MAG_ACTIVITY_MAP_EPOCH});
  const challenge=paidRegistrationChallenge(request,{payTo}),authorization={from:address(1),to:payTo,value:challenge.requirements.amount,nonce:'0x'+(x.rev==='A'?'4':'5').repeat(64),validAfter:String(Math.floor(now/1000)-1),validBefore:String(Math.floor(now/1000)+299)},input={request,challenge,normalized:{identity:{network:'eip155:8453',asset,authorizer:authorization.from,nonce:authorization.nonce},authorization},recoveryEnvelopeSha256:sha256Jcs({request,authorization})};
  const saved=await withRegistrationTiming({clock:()=>now,budgetValid:true,deadline:now+30000},async()=>{const prepared=await paidRegistration.prepare(input);await paidRegistration.recordProviderOutcome({intent:prepared.intent,outcome:{phase:'SETTLE',state:'OBSERVED',transactionHash:tx}});return paidRegistration.reconcile(prepared.intent.registration_intent_id);});
  if(saved.business_commit_state!=='COMMITTED')throw Error('Paid registration did not commit');return {registration:await paidRegistration.publicRecovery(saved),task_id:saved.task_id,purchase:{...purchase(),authorization_nonce:'0x'+(x.rev==='A'?'b':'c').repeat(64)}};
 }
 if(x.control==='paid-evaluate'){
  const e=await paidStore.get(x.task,`EXEC#${x.execution}`),w={state:'VERIFIED',reason:null,purchase:x.purchase,transaction_hash:'0x'+(x.rev==='A'?'a':'d').repeat(64),terms_digest:e.terms_digest,report_sha256:e.report_sha256,observed_at:new Date(now).toISOString()};
  await paidStore.transact([{put:{...e,version:e.version+1,witness:w},expectedVersion:e.version}]);const result=await paidEvaluation.finalize(x.task,x.execution);return {approved_amount_atomic:result.approved_amount_atomic};
 }
 if(x.control==='counts')return {settles,verifies,reads,now,transactions:persistence.stats.transactions};
 if(x.control==='discovery'){const {discoveryPage}=await import('../mag/discovery-page.mjs');return discoveryPage(root,physical,()=>now);}
 if(x.control==='economic'){const {conditionSnapshot}=await imp('backend/shared/task-venue/economic-terms.mjs');conditionSnapshot(x.terms,x.family);const PK=`TASK_ECONOMIC_TERMS#${x.family}`,old=await physical.getKey({table:'tasks',PK,SK:'CURRENT'});physical.seed({PK,SK:'CURRENT',version:(old?.version??0)+1,enabled:true,offers:[x.terms]},'tasks');return {};}
 if(x.control==='immediate-observation'){const {analyzeImmediateVisitResponse}=await imp('backend/shared/task-venue/immediate-visit-profile.mjs');return analyzeImmediateVisitResponse({status:404,headers:{'content-type':'application/json'},body:Buffer.from('{"id":1,"ok":true}'),profile_id:'immediate_visit_utf8.v3',fetch_started_at:new Date(now).toISOString(),fetch_finished_at:new Date(now+1).toISOString()});}
 if(x.control==='immediate-evaluate'){const {createImmediateVisitEvaluationService}=await imp('backend/shared/task-venue/immediate-visit-evaluation.mjs');const {createImmediateVisitReferenceService}=await imp('backend/shared/task-venue/immediate-visit-reference.mjs');const reference=createImmediateVisitReferenceService({utcClock:()=>now,monotonicClock:()=>now,fetcher:async()=>({status:404,headers:{'content-type':'application/json'},body:Buffer.from('{"id":1,"ok":true}')})});await createImmediateVisitEvaluationService({store:physical,clock:()=>now,reference}).evaluate({taskId:x.task,executionId:x.execution});return {};}
 if(x.path?.startsWith('/api/bazaar/task-offers/economic-terms/')){const {publicEconomicOffers}=await imp('backend/shared/task-venue/economic-terms.mjs');return {status:200,body:await publicEconomicOffers(physical,x.path.split('/').at(-1))};}
 if(x.control==='configure'){if(x.confirmed!==undefined)confirmed=x.confirmed;if(x.advance)now+=x.advance;if(x.contractCode!==undefined)contractCode=x.contractCode;if(x.contractValid!==undefined)contractValid=x.contractValid;if(x.rpcFails!==undefined)rpcFails=x.rpcFails;return {now};}
 if(x.control==='reconcile')return {state:(await (x.type?.startsWith('endpoint_choice_reason.')?choice:immediate).reconcile(x.ref)).business_commit_state};
 if(x.control==='evaluate'){evaluationQ=x.q??'0';await evaluation.run({task_id:x.task,execution_id:x.execution});return {};}
 if(x.control==='delete-descriptions'){return {deleted:await results.deleteDescriptions(x.task)};}
 const body=x.body?JSON.parse(x.body):null;
 if(x.path==='/api/bazaar/task-offers'){
  const h=body.task_type?.startsWith('endpoint_choice_reason.')?handlers.choice:handlers.immediate;
  const headers=Object.fromEntries(Object.entries(x.headers).map(([k,v])=>[k.toLowerCase(),v]));
  const ctx={get:n=>['choiceRequest','immediateVisitRequest'].includes(n)?body:undefined,req:{header:n=>headers[n.toLowerCase()],method:'POST',path:'/bazaar/task-offers'},json:(body,status,headers)=>({status,body,headers:headers??{}})};
  return withRegistrationTiming({budgetValid:true,deadline:Date.now()+120000},()=>h(ctx));
 }
 if(x.path.startsWith('/api/agent/task-offer-registration-recovery/'))return read(event(x));
 if(x.path.startsWith('/api/agent/task-offer-results/'))return ownerRead(event(x));
 const match=x.path.match(/^\/api\/agent\/tasks\/([^/]+)(.*)$/);
 if(match){
  const paidMeta=await paidStore.get(match[1]);
  if(paidMeta){if(match[2]==='')return {status:200,body:paidProjection(paidMeta)};const e=event(x);e.pathParameters={task_id:match[1]};const sub=match[2].match(/^\/submissions\/([^/]+)\/status$/);let op;if(match[2]==='/claim')op=Object.keys(x.headers).some(k=>k.toLowerCase()==='x-ln-claim-recovery')?'readClaim':'claim';else if(match[2]==='/completion')op='report';else if(sub){op='status';e.pathParameters.submission_id=sub[1];}else if(match[2]==='/claim/abandon')op='abandon';if(op)return createPaidExecutionHandler(paidExecution,op,paidMeta.task_definition_version==='3.2.0'?3.2:3.1)(e);}
  const im=await physical.get(match[1]);
  if(im){
   const headers=Object.fromEntries(Object.entries(x.headers).map(([k,v])=>[k.toLowerCase(),v]));
   const input={taskId:match[1],body,claimToken:headers['x-ln-task-claim-token'],idempotencyKey:headers['idempotency-key']};
   try{
    if(match[2]==='')return {status:200,body:immediateVisitTaskProjection(im,now)};
    if(match[2]==='/claim')return {status:200,body:await immediateExecution[headers['x-ln-claim-recovery']?'readClaim':'claim'](input)};
    if(match[2]==='/completion'){const r=await immediateExecution.report(input);return {status:r.http_status,body:r.body};}
    const sub=match[2].match(/^\/submissions\/([^/]+)\/status$/);
    if(sub)return {status:200,body:await immediateExecution.status({...input,submissionId:sub[1]})};
   }catch(e){return immediateVisitErrorResponse(e,'sdk',im);}
  }
  const e=event(x);e.pathParameters={task_id:match[1]};let action;
  if(match[2]==='')action='detail';
  else if(match[2]==='/claim')action=Object.keys(x.headers).some(k=>k.toLowerCase()==='x-ln-claim-recovery')?'claim-read':'claim';
  else if(match[2]==='/claim/abandon')action='abandon';
  else if(match[2]==='/completion')action='completion';
  else if(match[2]==='/results')action='results';
  else {const sub=match[2].match(/^\/submissions\/([^/]+)\/status$/);if(sub){action='status';e.pathParameters.submission_id=sub[1];}}
  if(action)return choiceHttp(action,e,{runtimeFactory:async()=>runtime});
 }
 throw Error('Unsupported isolated route');
}
const server=http.createServer(async(req,res)=>{let bytes='';for await(const chunk of req)bytes+=chunk;try{const u=new URL(req.url,'http://127.0.0.1');const control=u.pathname==='/_test/control';const x=control?JSON.parse(bytes||'{}'):{method:req.method,path:u.pathname,query:u.search.slice(1),headers:req.headers,body:bytes};const result=await run(x);if(control){res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(result));}else{res.writeHead(result.status??result.statusCode,result.headers??{'content-type':'application/json'});res.end(typeof result.body==='string'?result.body:JSON.stringify(result.body));}}catch(e){res.writeHead(500,{'content-type':'application/json'});res.end(JSON.stringify({bridge_error:e.message,stack:e.stack}));}});
server.listen(0,'127.0.0.1',()=>process.stdout.write(JSON.stringify({port:server.address().port})+'\n'));
process.on('SIGTERM',()=>{server.close();persistence.close();process.exit(0);});
