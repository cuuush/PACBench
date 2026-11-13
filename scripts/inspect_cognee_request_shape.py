"""Inspect pinned native request shape offline; abort before any network send."""
import os,json,asyncio,socket,tempfile,pathlib,argparse
from importlib.metadata import version
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=pathlib.Path,required=True);parsed=parser.parse_args()
if version('cognee')!='1.6.8':raise RuntimeError('This probe is bound to pinned Cognee1.6.8')
os.environ.update(TELEMETRY_DISABLED='1',LITELLM_LOCAL_MODEL_COST_MAP='True',LLM_PROVIDER='openai',LLM_MODEL='openai/deepseek/deepseek-v4.1-flash-1017',LLM_ENDPOINT='https://openrouter.ai/api/v1',LLM_API_KEY='offline-not-a-credential',LLM_INSTRUCTOR_MODE='json_schema_mode')
os.environ.pop('OPENROUTER_API_KEY',None)
for name in list(os.environ):
 if name=='PACBENCH_DIRECT' or name.startswith('PACBENCH_DIRECT_'):os.environ.pop(name,None)
private=tempfile.TemporaryDirectory(prefix='cognee-offline-probe-');os.environ.update(SYSTEM_ROOT_DIRECTORY=private.name+'/system',DATA_ROOT_DIRECTORY=private.name+'/data',CACHE_ROOT_DIRECTORY=private.name+'/cache')
class BlockWire(BaseException):pass
network=[]
def no_connect(*a,**kw):network.append('socket-connect-blocked');raise BlockWire()
socket.socket.connect=no_connect
import httpx
captured=[]
async def send(self,request,*a,**kw):
 captured.append({'url':str(request.url),'body':json.loads(request.content)});raise BlockWire()
httpx.AsyncClient.send=send
from pydantic import BaseModel
from cognee.infrastructure.llm.structured_output_framework.litellm_native.native_adapter import NativeLiteLLMAdapter
class Result(BaseModel):answer:str
async def probe(args):
 a=NativeLiteLLMAdapter(api_key='offline-not-a-credential',model=os.environ['LLM_MODEL'],max_completion_tokens=16384,endpoint=os.environ['LLM_ENDPOINT'],llm_args=args)
 try:await a._acreate_structured('Offline request-shape probe, not benchmark input.','Offline probe. No model request may leave this process.',Result,model=a.model,api_key=a.api_key,endpoint=a.endpoint,api_version=a.api_version,**a.llm_args)
 except BlockWire:pass
 except Exception as e:return {'error_class':type(e).__name__,'captured':len(captured),'error':str(e)[:180]}
 body=captured[-1]['body'] if captured else {};keys=['reasoning','reasoning_effort','max_tokens','max_completion_tokens','temperature']
 return {'captured_at_httpx_send_before_network':bool(captured),'model':body.get('model'),'request_keys':sorted(body),'parameters':{k:{'present':k in body,'value':body.get(k)} for k in keys}}
async def main():
 result={}
 for name,args in [('native-default',{}),('documented-llm-args-probe',{'reasoning_effort':'low','max_completion_tokens':16384})]:
  captured.clear();result[name]=await probe(args)
 result.update(paid_calls=0,socket_connect_attempts=len(network),scope='Reproduction with dummy prompt and key; not the missing historical request body. Both HTTPX send and socket connect abort before network.')
 parsed.output.write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
try:asyncio.run(main())
finally:private.cleanup()
