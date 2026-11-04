// Fixed c1 owner/runtime; SQLite, synthetic provider/finality/quality adapters.
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';
import {writeFileSync,readFileSync} from 'node:fs';
import {createInterface} from 'node:readline';
import crypto from 'node:crypto';
const root=(process.env.MAG_BACKEND_C3_ROOT??process.env.MAG_BACKEND_C2_ROOT??process.env.MAG_BACKEND_C1_ROOT),imp=p=>import(pathToFileURL(join(root,p)));
process.env.MAG_ACTIVITY_MAP_EPOCH='synthetic-C';
globalThis.fetch=()=>{throw Error('External network forbidden');};
for(const k of ['log','info','warn','debug','error'])console[k]=()=>{};
const {startFixture}=await imp('tests/template-survey-sharing/backend-c3/http-fixture.mjs');
const {createChoiceRegistrationService}=await imp('backend/shared/task-venue/endpoint-choice-reason-registration.mjs');
const {structuredChallenge}=await imp('backend/shared/task-venue/structured-response-registration.mjs');
const {withRegistrationTiming}=await imp('backend/shared/task-venue/registration-timing.mjs');
const {finality}=await imp('tests/fixtures/v189/registration-evidence.mjs');
const {createStructuredEvaluation}=await imp('backend/shared/task-venue/structured-response-evaluation.mjs');
const initial=JSON.parse(readFileSync(join(root,'backend/shared/task-venue/economic-initial-offers.json')));
const s=await startFixture({directory:process.env.LN_TEST_STATE_DIR,seed:false});
const policy={activity_map_policy_id:'mag.activity-map.public.v1',activity_map_epoch:'synthetic-C'};
for(const row of initial.rows)s.f.physical.seed({...row,enabled:true},'tasks');
async function register(revision='A'){
 const current=await s.f.store.getKey({table:'tasks',PK:'TASK_ECONOMIC_TERMS#structured',SK:'CURRENT'});
 const terms=structuredClone(current.offers.find(o=>o.plan_id==='C5'));
 terms.economic_terms_revision='sdk-mag-'+revision;
 if(revision==='B'){terms.capacity_total=7;terms.reward.normal_amount_atomic='123456';terms.reward.compensation_amount_atomic='6543';terms.registration_amount_atomic='9876543';terms.unfilled_refund_each_atomic='123456';}
 s.f.physical.seed({...current,version:current.version+1,offers:[terms]},'tasks');
 const registration={task_type:'structured_response.v3',title:'Synthetic custom report',description:'A same-ID generic report.',
  items:[{item_id:'a1_1',question:{en:'Report the declared custom boolean.'},kind:'report',required:true,allowed_statuses:['answered','unknown'],quality_condition:'The answer responds to the declared question.',content_conditions:[],report_fields:[{field_id:'custom',type:'boolean',required:true}]}],participation:{mode:'ANY'},private_reference_items:[]};
 const quote=await s.f.r.orders.customQuote({schema_version:'ln_church.structured_response_quote_request.v3_2',...policy,
  quote_key:'generic_'+crypto.randomBytes(10).toString('hex'),quote_token:crypto.randomBytes(32).toString('hex'),registration,plan_id:terms.plan_id,economic_terms:terms});
 const request={schema_version:'ln_church.task_offer_registration_request.structured_response.v3_2',...policy,
  economic_terms_revision:quote.economic_terms_revision,economic_terms_digest:quote.economic_terms_digest,
  registration_profile:'STRUCTURED_RESPONSE_V3',quote_id:quote.quote_id,quote_token:quote.quote_token,confirmed_preview_revision:quote.preview.preview_revision};
 const q=await s.f.r.orders.readQuote(quote.quote_id,quote.quote_token),payTo='0x'+'1'.repeat(40),payer='0x'+'2'.repeat(40);
 const challenge=structuredChallenge(q,payTo),now=s.f.clock();
 const authorization={from:payer,to:payTo,value:quote.pricing.total_atomic,nonce:'0x'+crypto.randomBytes(32).toString('hex'),validAfter:String(Math.floor(now/1000)-1),validBefore:String(Math.floor(now/1000)+299)};
 const identity={network:'eip155:8453',asset:challenge.requirements.asset,authorizer:payer,nonce:authorization.nonce};
 const service=createChoiceRegistrationService({store:s.f.store,clock:s.f.clock,structuredOrders:s.f.r.orders,chainReadback:async i=>finality(i)});
 const prepared=await withRegistrationTiming({clock:s.f.clock,budgetValid:true,deadline:now+60000},()=>service.prepare({request,challenge,normalized:{identity,authorization},recoveryEnvelopeSha256:'b'.repeat(64)}));
 await service.recordProviderOutcome({intent:prepared.intent,outcome:{phase:'SETTLE',state:'OBSERVED',transactionHash:'0x'+'a'.repeat(64)}});
 const committed=await service.reconcile(prepared.intent.registration_intent_id);
 if(committed.business_commit_state!=='COMMITTED')throw Error('Registration did not commit');
 const row={task_id:quote.reservations[0].task_id,version:'3.2.0',kind:'CUSTOM',revision};s.tasks.push(row);return row;
}
if(!s.tasks.length)await register();
const access=join(s.directory,'local-access.json');writeFileSync(access,JSON.stringify({origin:s.origin,headers:{'x-ln-access-origin-auth':s.secret}}),{mode:0o600});
process.stdout.write('SDK_MAG_READY '+JSON.stringify({access_file:access,tasks:s.tasks,pid:process.pid})+'\n');
for await(const line of createInterface({input:process.stdin})){
 const command=JSON.parse(line);
 if(command.action==='switch_B')process.stdout.write('SDK_MAG_REPLY '+JSON.stringify(await register('B'))+'\n');
 else if(command.action==='discovery'){
  const {discoveryPage}=await import('./discovery-page.mjs');
  process.stdout.write('SDK_MAG_REPLY '+JSON.stringify(await discoveryPage(root,s.f.physical,s.f.clock))+'\n');
 }
 else if(command.action==='evaluate'){
  const evaluation=createStructuredEvaluation({store:s.f.store,content:s.f.content,execution:s.f.r.execution,clock:s.f.clock,
   contextRead:(m,e)=>s.f.r.projector.prior(m,e),adapter:async()=>({value:command.q,returned_model:'jev-1.13.0'})});
  for(let n=0;n<20;n++){await evaluation.run({task_id:command.task_id,execution_id:command.execution_id});const e=await s.f.store.get(command.task_id,'EXEC#'+command.execution_id);
   if(e.entitlement.state==='FINAL'){process.stdout.write('SDK_MAG_REPLY '+JSON.stringify({entitlement:e.entitlement})+'\n');break;}if(n===19)throw Error('Evaluation incomplete');}
 }else if(command.action==='close'){await s.close();process.exit(0);}
 else throw Error('Unknown command');
}
