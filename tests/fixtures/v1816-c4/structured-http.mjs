// Test adapter only. All registration, generation and execution use fixed c4 owners.
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';
import {readFileSync,writeFileSync} from 'node:fs';
import {createInterface} from 'node:readline';
const root=process.env.LN_CHURCH_C4_ROOT;
const {startFixture}=await import(pathToFileURL(join(root,'tests/template-survey-sharing/backend-c3/http-fixture.mjs')));
const {prepare}=await import(pathToFileURL(join(root,'tests/template-survey-sharing/backend-c3/fixture.mjs')));
const {createStructuredEvaluation}=await import(pathToFileURL(join(root,'backend/shared/task-venue/structured-response-evaluation.mjs')));
const s=await startFixture({directory:process.env.LN_TEST_STATE_DIR});
const access=join(s.directory,'local-access.json');
writeFileSync(access,JSON.stringify({origin:s.origin,headers:{'x-ln-access-origin-auth':s.secret}}),{mode:0o600});
console.log('SDK_C4_READY '+JSON.stringify({access_file:access,tasks:s.tasks,pid:process.pid}));
for await(const line of createInterface({input:process.stdin})){
 const command=JSON.parse(line);
 if(command.action==='switch_B'){
  const row=await s.f.store.getKey({table:'tasks',PK:'TASK_ECONOMIC_TERMS#structured',SK:'CURRENT'});
  const offer=structuredClone(row.offers.find(o=>o.plan_id==='C5'));
  offer.economic_terms_revision='sdk-c4-B';offer.capacity_total=7;
  offer.reward.normal_amount_atomic='123456';offer.reward.compensation_amount_atomic='6543';
  offer.registration_amount_atomic='9876543';offer.unfilled_refund_each_atomic='123456';
  s.f.physical.seed({...row,version:row.version+1,offers:[offer]},'tasks');
  const quote=await prepare(s.f,{economic_terms:offer}),order=await s.f.purchase(quote),task=quote.reservations[0].task_id;
  await s.f.r.generation.generate({order_id:order.order_id,task_id:task});
  s.tasks.push({task_id:task,version:'3.1.0',kind:'DISCLOSED',revision:'B'});
  console.log('SDK_C4_REPLY '+JSON.stringify({task_id:task}));
 }else if(command.action==='evaluate'){
  const evaluation=createStructuredEvaluation({store:s.f.store,content:s.f.content,execution:s.f.r.execution,
   clock:s.f.clock,contextRead:(m,e)=>s.f.r.projector.prior(m,e),adapter:async()=>({value:command.q,returned_model:'jev-1.13.0'})});
  for(let n=0;n<20;n++){
   await evaluation.run({task_id:command.task_id,execution_id:command.execution_id});
   const saved=await s.f.store.get(command.task_id,'EXEC#'+command.execution_id);
   if(saved.entitlement.state==='FINAL'){console.log('SDK_C4_REPLY '+JSON.stringify({entitlement:saved.entitlement}));break;}
   if(n===19)throw Error('Evaluation did not finalize');
  }
 }else if(command.action==='close'){await s.close();process.exit(0);}
 else throw Error('Unknown test command');
}
