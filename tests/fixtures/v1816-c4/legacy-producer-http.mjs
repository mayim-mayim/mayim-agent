// Original dbd2cf7 HTTP services create the accepted operations.
// Persist exact SQLite and object state for a separate c4 process; no Task fabrication.
import http from 'node:http';
import {mkdirSync,copyFileSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
const root=process.env.HONDO_ROOT;
const imp=p=>import(pathToFileURL(root+'/'+p).href);
globalThis.fetch=()=>{throw Error('External network prohibited');};
console.log=()=>{};console.info=()=>{};console.warn=()=>{};
const {fixture,prepare}=await imp('tests/template-survey-sharing/backend-c2/structured-fixture.mjs');
const {structuredHttp}=await imp('backend/shared/task-venue/structured-response-runtime.mjs');
const f=fixture();Date.now=()=>f.clock();
const directory=process.env.LN_TEST_STATE_DIR;mkdirSync(directory,{recursive:true});
mkdirSync(join(directory,'objects'),{recursive:true});
const {persistentObjects}=await imp('tests/template-survey-sharing/backend/persistent-fixture.mjs');
Object.assign(f.content,persistentObjects(join(directory,'objects')));
const quote=await prepare(f),order=await f.purchase(quote),taskId=quote.reservations[0].task_id;
await f.r.generation.generate({order_id:order.order_id,task_id:taskId});
const server=http.createServer(async(req,res)=>{
 try{
  const chunks=[];for await(const chunk of req)chunks.push(chunk);
  const body=Buffer.concat(chunks).toString(),url=new URL(req.url,'http://127.0.0.1');
  if(url.pathname==='/__control'){
   const control=JSON.parse(body);
   if(control.advance)f.advance(control.advance);
   const meta=await f.store.get(taskId);
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({now:f.clock(),
    accepted_count:meta.accepted_count,rewards_paid_confirmed:meta.rewards_paid_confirmed,
    transactions:f.persistence.stats.transactions}));return;
  }
  const match=url.pathname.match(/^\/api\/agent\/tasks\/([^/]+)(?:\/(claim|completion|context|abandon)|\/submissions\/([^/]+)\/status)?$/);
  if(!match){res.writeHead(404);res.end('{}');return;}
  let action=match[3]?'status':match[2]??'detail';
  if(action==='claim'&&req.headers['x-ln-claim-recovery']==='1')action='claim-read';
  const event={rawPath:url.pathname,rawQueryString:url.search.slice(1),headers:req.headers,body,
   pathParameters:{task_id:match[1],submission_id:match[3]},requestContext:{http:{method:req.method},requestId:'isolated'}};
  const result=await structuredHttp(action,event,{runtimeFactory:async()=>f.r,diagnostic:()=>{}});
  res.writeHead(result?.statusCode??404,result?.headers??{});res.end(result?.body??'{}');
 }catch(error){res.writeHead(500);res.end(JSON.stringify({error:error.message}));}
});
server.listen(0,'127.0.0.1',()=>process.stdout.write(JSON.stringify({port:server.address().port,task_id:taskId})+'\n'));
process.on('SIGTERM',()=>server.close(()=>{f.persistence.close();copyFileSync(join(f.persistence.directory,'store.sqlite'),join(directory,'store.sqlite'));writeFileSync(join(directory,'fixture-state.json'),JSON.stringify({clock:f.clock(),tasks:[{task_id:taskId,version:'3.0.0',kind:'DISCLOSED'}]}));process.exit(0);}));
