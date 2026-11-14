// Native public MemOS bootstrap + HTTP transport; all memory algorithms stay
// in the pinned upstream package. This is the Hermes viewer's read-oriented
// role, with a run-owned dynamic loopback port and no viewer singleton/reaping.
import {bootstrapMemoryCoreFull} from '../vendor/memos-local-plugin/package/dist/core/pipeline/index.js';
import {startHttpServer} from '../vendor/memos-local-plugin/package/dist/server/index.js';
const port=Number(process.argv[2]);
if(!Number.isInteger(port)||port<1||port>65535)throw new Error('Explicit loopback port required');
const {core,home}=await bootstrapMemoryCoreFull({agent:'hermes',
  namespace:{agentKind:'hermes',profileId:'default'},autoRecovery:false,
  pkgVersion:'2.0.21',hostLlmBridge:null,initLogging:true});
// The pinned bootstrap constructs core but does not call its public init().
await core.init();
const server=await startHttpServer({core,home,logTail:()=>[]},
  {host:'127.0.0.1',port,agent:'hermes'});
process.stdout.write(JSON.stringify({endpoint:server.url,pid:process.pid,home:home.root})+'\n');
let stopping=false;
async function stop(){if(stopping)return;stopping=true;await server.close();await core.shutdown();}
process.on('SIGTERM',()=>void stop().then(()=>process.exit(0)));
process.on('SIGINT',()=>void stop().then(()=>process.exit(0)));
