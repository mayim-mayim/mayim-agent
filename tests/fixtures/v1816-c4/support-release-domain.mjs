// Fixture-only deployment configuration. No Product source or response changes.
// The exact support uses manifest.example; the SDK pins its public release host.
// Change both the fixture's environment and its signed-policy assertion together.
import {registerHooks} from 'node:module';
registerHooks({load(url,context,next){
 const result=next(url,context);
 if(url.endsWith('/backend-c4-support/runtime.mjs')){
  const source=String(result.source),matches=source.match(/manifest\.example/g)||[];
  if(matches.length!==2)throw Error('Unexpected support release-domain fixture');
  return {...result,source:source.replaceAll('manifest.example','tasks-release.mayim-mayim.com')};
 }
 // Match the existing public SDK paths to the support's same actual owners.
 // Its probe aliases omit /status and use different completion/readiness names.
 if(url.endsWith('/backend-c4-support/http-fixture.mjs')){
  const source=String(result.source),matches=source.match(/manifest-readiness/g)||[];
  if(matches.length!==2)throw Error('Unexpected support readiness route fixture');
  if((source.match(/completion-reports/g)||[]).length!==2 || !source.includes('submissions\\/([^/]+)'))throw Error('Unexpected support completion/status fixture');
  return {...result,source:source.replaceAll('manifest-readiness','readiness')
   .replaceAll('completion-reports','completion').replace('submissions\\/([^/]+)','submissions\\/([^/]+)\\/status')};
 }
 return result;
}});
