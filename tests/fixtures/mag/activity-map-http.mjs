// Fixed Backend control/http/payment, local HTTP; only storage/content/verifier
// and grant transaction runner are asset-free doubles. No external network.
import http from 'node:http';
import {pathToFileURL} from 'node:url';
const root=(process.env.MAG_BACKEND_C3_ROOT??process.env.MAG_BACKEND_C2_ROOT??process.env.MAG_BACKEND_C1_ROOT);
const imp=p=>import(pathToFileURL(root+'/backend/shared/'+p));
const {createControl,key,digest,PREFIX}=await imp('agent-activity-map/control.mjs');
const {createHttp}=await imp('agent-activity-map/http.mjs');
const {paymentAdapter}=await imp('agent-activity-map/payment.mjs');
globalThis.fetch=()=>{throw Error('External network forbidden');};
const rows=new Map(),objects=new Map(),calls=[];
const k=x=>x.PK+'|'+x.SK,clone=x=>x===undefined?undefined:structuredClone(x);
let now=Date.parse('2026-11-01T00:00:00Z'),verifications=0,settlements=0;
let challenge={status:'payment_required',synthetic:true},challengeHeaders={};
let dropComplete=false,failPart=false,loseComplete=false,losePurchase=false,stopAtComplete=false,expireAtComplete=false;
const secret='synthetic-origin-access-not-for-production';
const store={read:async keys=>keys.map(x=>clone(rows.get(k(x)))),transact:async ops=>{
 for(const op of ops){const x=op.put??op.check;if((rows.get(k(x))?.version??null)!==op.expected)throw Error('Conflict');}
 for(const op of ops)if(op.put)rows.set(k(op.put),clone(op.put));
}};
function seed(x){rows.set(k(x),{...x,version:(rows.get(k(x))?.version??0)+1});}
const window={start:'2026-10-02T15:00:00.000Z',end:'2026-11-01T15:00:00.000Z',timezone:'Asia/Tokyo'};
const descriptor=b=>({part_id:digest(b),sha256:digest(b),bytes:b.length});
function generation(id,previous){
 const data=Buffer.from('{"items":["日本語",1]}\n');
 const catalog=Buffer.from(JSON.stringify({schema_version:'mag.activity-map.part-catalog.v1',parts:[descriptor(data)]}));
 const manifest=Buffer.from(JSON.stringify({schema_version:'mag.activity-map.manifest.v1',generation:id,period:'30d',window,
  windows:Object.fromEntries(['today','7d','30d'].map(p=>[p,window])),cutoff:'2026-11-01T00:00:00.000Z',policy_id:'mag.activity-map.public.v1',
  source_coverage:{scheduled:'not_confirmed'},coverage_complete:false,parts:[descriptor(catalog)],parts_format:'one-level-catalog',
  uncompressed_encoding:'UTF-8',hash_algorithm:'SHA-256',total_known:null}));
 const g={...key('GENERATION',id),generation:id,manifest_sha256:digest(manifest),window,cutoff:'2026-11-01T00:00:00.000Z',
  schema:'mag.activity-map.manifest.v1',policy_id:'mag.activity-map.public.v1',period:'30d',state:'AVAILABLE',replacement_of:previous??null};
 seed(g);objects.set(id,new Map([['manifest',manifest],[digest(catalog),catalog],[digest(data),data]]));return g;
}
const original=generation('a'.repeat(32));seed({...key('CONTROL','CURRENT'),generation:original.generation});seed({...key('CONTROL','SAFETY'),blocked:false});
const control=createControl({store,clock:()=>now});
const verifier=async(event,options)=>{
 verifications++;const h=Object.fromEntries(Object.entries(event.headers).map(([k,v])=>[k.toLowerCase(),v]));const body=JSON.parse(event.body);
 if(options.expectedRoute!==PREFIX+'/purchases'||options.allowFaucet!==false)throw Error('Adapter boundary');
 let rail,network,asset,reference;
 if(body.paymentOverride?.type==='grant'){if(body.agentId!=='synthetic-agent')throw Error('Grant Agent binding missing');rail='grant';network='offchain:grant';asset='GRANT_CREDIT';reference='synthetic-grant-jti-0001';}
 else if(h.authorization?.startsWith('L402 ')){rail='L402';network='lightning';asset='SATS';reference='1'.repeat(64);}
 else if(h.authorization?.startsWith('Payment ')){rail='mpp';network='eip155:8453';asset='USDC';reference='0x'+'2'.repeat(64);}
 else if(h['payment-signature']){rail='x402';network='eip155:8453';asset='USDC';reference='0x'+'3'.repeat(64);}
 else return {isAuthorized:false,errorResponse:{statusCode:402,headers:challengeHeaders,body:JSON.stringify(challenge)}};
 if(h.authorization?.startsWith('MAG-Recovery '))throw Error('Recovery credential leaked into payment verifier');
 return {isAuthorized:true,actualPaymentMethod:rail,verifyResult:{isValid:true,network,asset,settlementReference:reference,grantJti:reference}};
};
const payment=paymentAdapter({verifyOrChallengePayment:verifier,runGrantOperation:async(p,fn)=>{settlements++;return fn(null);}});
const content={descriptor:async(g,id)=>{const b=objects.get(g.generation)?.get(id);return b?{...descriptor(b),id}:null;},bytes:async(g,d)=>objects.get(g.generation).get(d.id)};
const handler=createHttp({control,payment,content,originSecret:secret,reader:async()=>({})});
const server=http.createServer(async(req,res)=>{try{
 const chunks=[];for await(const c of req)chunks.push(c);const body=Buffer.concat(chunks).toString();
 if(req.url==='/_test/control'){
  const command=JSON.parse(body);Object.assign({},command);
  if(command.action==='challenge'){challenge=command.body;challengeHeaders=command.headers??{};}
  if(command.action==='fault'){dropComplete=Boolean(command.dropComplete);failPart=Boolean(command.failPart);loseComplete=Boolean(command.loseComplete);losePurchase=Boolean(command.losePurchase);stopAtComplete=Boolean(command.stopAtComplete);expireAtComplete=Boolean(command.expireAtComplete);}
  if(command.action==='advance')now+=command.ms;
  if(command.action==='revoke'){
   await control.revoke(original.generation,'synthetic-stop');
   if(command.replacement){const g=generation('b'.repeat(32),original.generation);const old=rows.get(k(key('GENERATION',original.generation)));seed({...old,replacement:{generation:g.generation,manifest_sha256:g.manifest_sha256,reason:'synthetic correction'}});}
  }
  const purchases=[...rows.values()].filter(r=>r.PK==='MAG#PURCHASE').map(p=>({operation_id:p.operation_id,state:p.state,payment_digest:p.payment_digest}));
  res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify({now,calls,verifications,settlements,purchases}));return;
 }
 const path=req.url.split('?')[0];calls.push({method:req.method,path});
 if(dropComplete&&path.endsWith('/complete')){req.socket.destroy();return;}
 if(failPart&&path.includes('/parts/')){res.writeHead(503);res.end('{}');return;}
 if(path.endsWith('/complete')){if(stopAtComplete)await control.revoke(original.generation,'synthetic-complete-race');if(expireAtComplete)now=Date.parse('2026-11-02T00:00:00Z');}
 const response=await handler({rawPath:path,rawQueryString:req.url.split('?')[1]??'',headers:{...req.headers,'x-mag-origin-auth':secret},body,requestContext:{http:{method:req.method}}});
 if((loseComplete&&path.endsWith('/complete'))||(losePurchase&&path.endsWith('/purchases')&&response.statusCode!==402)){req.socket.destroy();return;}
 res.writeHead(response.statusCode,response.headers);res.end(response.isBase64Encoded?Buffer.from(response.body,'base64'):response.body);
}catch{res.writeHead(500);res.end('{"error":"isolated_fixture_failure"}');}});
server.listen(0,'127.0.0.1',()=>process.stdout.write(JSON.stringify({port:server.address().port})+'\n'));
