// Fixed c3 Python producer -> actual content/reader/control/payment -> SDK HTTP.
// Physical storage, upstream activity inputs and settlement are asset-free doubles.
import http from 'node:http';
import {execFileSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
const root=process.env.MAG_BACKEND_C4_ROOT??process.env.MAG_BACKEND_C3_ROOT;
const c4=process.env.MAG_C4_SOURCE;
const fixtures=JSON.parse(execFileSync(process.env.MAG_TEST_PYTHON??'python',[root+(c4?'/tests/mag-backend-c4/export_task_fixture.py':'/tests/mag-backend-c3/producer_fixture.py')],{cwd:root,maxBuffer:12*1024*1024}));
const producer=c4?fixtures[c4]:fixtures;
const imp=p=>import(pathToFileURL(root+'/backend/shared/agent-activity-map/'+p+'.mjs'));
const {createControl,key,PREFIX,MapError}=await imp('control');
const {createContent}=await imp('content');const {createReader,cursorCodec}=await imp('reader');
const {createHttp}=await imp('http');const {paymentAdapter}=await imp('payment');
globalThis.fetch=()=>{throw Error('External network forbidden');};
const rows=new Map(),calls=[],k=x=>x.PK+'|'+x.SK,save=x=>rows.set(k(x),structuredClone(x));
producer.before.forEach(save);
let now=Date.parse(producer.clock??'2026-11-01T01:00:00Z'),verifications=0,settlements=0,stopped=false,stopAtPayment=true,failPart=false;
function stop(){if(!stopped){producer.after.forEach(save);now+=3600000;stopped=true;}}
const store={read:async keys=>keys.map(x=>structuredClone(rows.get(k(x))??null)),transact:async ops=>{
 for(const op of ops)if((rows.get(k(op.put??op.check))?.version??null)!==op.expected)throw new MapError('control_changed',409);
 for(const op of ops)if(op.put)save(op.put);
}};
const control=createControl({store,clock:()=>now});
const content=createContent({store,objects:{get:async key=>{if(!(key in producer.objects)){const e=Error();e.name='NoSuchKey';throw e;}return Buffer.from(producer.objects[key]);}}});
const reader=createReader({content,cursors:cursorCodec('synthetic-sdk-c2-cursor-secret-0001'),maxReads:3,pageSize:10});
const payment=paymentAdapter({verifyOrChallengePayment:async event=>{
 verifications++;const h=Object.fromEntries(Object.entries(event.headers).map(([k,v])=>[k.toLowerCase(),v]));
 if(!h['payment-signature'])return {isAuthorized:false,errorResponse:{statusCode:402,headers:{},body:'{"synthetic":true}'}};
 if(stopAtPayment)stop();
 return {isAuthorized:true,actualPaymentMethod:'x402',verifyResult:{isValid:true,network:'eip155:8453',asset:'USDC',settlementReference:'0x'+'3'.repeat(64)}};
},runGrantOperation:async(p,fn)=>{settlements++;return fn(null);}});
const secret='synthetic-sdk-c2-origin-secret-0001',handler=createHttp({control,payment,content,reader,originSecret:secret});
const server=http.createServer(async(req,res)=>{try{
 const chunks=[];for await(const chunk of req)chunks.push(chunk);const body=Buffer.concat(chunks).toString();
 if(req.url==='/_test/control'){
  const cmd=JSON.parse(body);
  if(cmd.action==='fault'){failPart=Boolean(cmd.failPart);if(cmd.stopAtPayment!==undefined)stopAtPayment=cmd.stopAtPayment;}
  if(cmd.action==='stop')stop();
  if(cmd.action==='advance')now+=cmd.ms;
  const purchases=[...rows.values()].filter(x=>x.PK==='MAG#PURCHASE').map(x=>({operation_id:x.operation_id,state:x.state,generation:x.generation,payment_digest:x.payment_digest,confirmed_at:x.confirmed_at,expires_at:x.expires_at}));
  const page=stopped?await reader({generation:rows.get(k(key('GENERATION',producer.new))),kind:'activities',params:{}}):null;
  res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify({now,calls,verifications,settlements,purchases,old:producer.old,new:producer.new,removed:producer.removed,retained:producer.retained,added:producer.added,reader_page:page,payment_bindings:[...rows.values()].filter(x=>x.PK==='MAG#PAYMENT').length}));return;
 }
 const path=req.url.split('?')[0];calls.push({method:req.method,path});
 if(failPart&&path.includes('/parts/')){res.writeHead(503);res.end('{}');return;}
 const result=await handler({rawPath:path,rawQueryString:req.url.split('?')[1]??'',headers:{...req.headers,'x-mag-origin-auth':secret},body,requestContext:{http:{method:req.method}}});
 res.writeHead(result.statusCode,result.headers);res.end(result.isBase64Encoded?Buffer.from(result.body,'base64'):result.body);
}catch(e){process.stderr.write(String(e)+'\n');res.writeHead(500);res.end('{"error":"producer_fixture_failure"}');}});
server.listen(0,'127.0.0.1',()=>process.stdout.write(JSON.stringify({port:server.address().port})+'\n'));
