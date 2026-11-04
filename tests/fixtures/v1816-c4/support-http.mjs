// External test requester over the unmodified shared c4-support HTTP client.
import {pathToFileURL} from 'node:url';import {join} from 'node:path';import {createInterface} from 'node:readline';
const root=process.env.LN_CHURCH_C4_SUPPORT_ROOT;
process.env.NODE_OPTIONS=(process.env.NODE_OPTIONS||'')+' --import='+new URL('./support-release-domain.mjs',import.meta.url).href;
const c=await import(pathToFileURL(join(root,'tests/template-survey-sharing/backend-c4-support/client.mjs')));
let h=await c.host();
const a=await c.scheduled(h);if(a.paid.status!==201)throw Error('A did not register');
const terms={...a.terms,economic_terms_revision:'sdk-support-B',plan_id:'opaque-B',registration_amount_atomic:'2000000',capacity_total:80,
 reward:{...a.terms.reward,base_amount_atomic:'17000',reference_amount_atomic:'230000'}};
await c.local(h,'offers',{family:'scheduled',offers:[terms]});
const o=await c.origin(h),m=c.multipart(terms,(await c.local(h,'state')).body.clock),unpaid=await c.call(h,'/api/bazaar/task-offers',m.body,{...o.auth,...m.headers});
if(unpaid.status!==402)throw Error('B did not challenge');
const payment=await c.sign(JSON.parse(Buffer.from(unpaid.headers['payment-required'],'base64')),c.wallet(),(await c.local(h,'state')).body.clock);
const b=await c.call(h,'/api/bazaar/task-offers',m.body,{...o.auth,...m.headers,'payment-signature':payment});if(b.status!==201)throw Error('B did not register');
console.log('SDK_SUPPORT_READY '+JSON.stringify({access_file:h.access_file,backend_pid:h.pid,
 tasks:[{task_id:a.paid.body.task_id,terms:a.terms},{task_id:b.body.task_id,terms}],conditions_input:'explicit fixture seed; public GET B01 retained'}));
for await(const line of createInterface({input:process.stdin})){
 const command=JSON.parse(line);
 if(command.action==='restart'){const directory=h.directory;await h.close();h=await c.host(directory);console.log('SDK_SUPPORT_REPLY '+JSON.stringify({access_file:h.access_file,backend_pid:h.pid}));}
 else if(command.action==='close'){await h.close();process.exit(0);}
 else throw Error('Unknown test command');
}
