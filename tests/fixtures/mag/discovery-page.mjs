import {pathToFileURL} from 'node:url';
export async function discoveryPage(root,physical,clock){
 const imp=p=>import(pathToFileURL(root+'/backend/shared/task-discovery/'+p));
 const {CONTROL,FAMILIES,AVAILABLE,sourceFamily}=await imp('registry.mjs');
 const {createDiscoveryService}=await imp('reader.mjs');
 const {initializationTransaction}=await imp('writer.mjs');
 // The retained SQLite fixture omits the production DocumentClient wrapper.
 // Exercise the fixed initialization projection from actual committed owner
 // rows; this is not a test of atomic production dual-write composition.
 for(const [key,source] of [...physical.rows])if(sourceFamily(source)){
  const tx=initializationTransaction({table:'tasks',sourceTable:key.split('|')[0],source,at:clock()});
  for(const item of tx.TransactItems)if(item.Put)physical.seed(item.Put.Item,'tasks');
 }
 physical.seed({...CONTROL,schema_version:'ln_church.task_discovery_control.v1',generation:'v1',state:'READY',
  initialized_at:new Date(clock()).toISOString(),families:FAMILIES,version:1},'tasks');
 const docClient={send:async input=>{
  if(input.Key)return {Item:await physical.getKey({...input.Key,table:'tasks'})};
  const items=[...physical.rows.entries()].filter(([key,row])=>key.startsWith('tasks|')&&row.PK===AVAILABLE&&row.SK>=input.ExpressionAttributeValues[':after'])
   .map(([,row])=>row).sort((a,b)=>a.SK.localeCompare(b.SK));
  return {Items:items.slice(0,input.Limit)};
 }};
 return createDiscoveryService({docClient,clock}).list({queryStringParameters:{limit:'50'}});
}
