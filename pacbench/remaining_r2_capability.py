"""Versioned remaining-arm native preflight; never grants question dispatch."""
import os,json,hashlib
from pathlib import Path
from . import full_run_capability as frozen
from .gateway_reuse import atomic
from .hermes_gateway import GatewayLifecycleError
ENV='PACBENCH_REMAINING_R2'
PROVIDERS=('hindsight','supermemory','openviking','honcho','cognee','memos')
PROTOCOL='remaining-r2-native-preflight-v1'

def create(root,provider):
 root=Path(root).resolve()
 if provider not in PROVIDERS:raise GatewayLifecycleError('Remaining enabled provider required')
 runid=hashlib.sha256(str(root).encode()).hexdigest()[:12]
 c={'protocol':PROTOCOL,'root':str(root),'provider':provider,'frozen_inputs':frozen.frozen()[3]['frozen_inputs'],
  'projects':{provider:{r:f'pacbench-{runid}-{provider}-{r}' for r in ['driver','memory','judge']}},
  'model':'openai/gpt-6-luna','profile':'luna-bedrock-global','endpoint':'amazon-bedrock/global',
  'slot_path':str(root/'active-slot.json'),'questions_enabled':False}
 atomic(root/'capability.json',c);return frozen.binding(root/'capability.json')

def load(b=None):
 b=b or json.loads(os.environ[ENV]);c=frozen.read(b)
 if (c['protocol']!=PROTOCOL or c['provider'] not in PROVIDERS or c['questions_enabled'] is not False or
     c['frozen_inputs']!=frozen.frozen()[3]['frozen_inputs'] or c['model']!='openai/gpt-6-luna' or
     c['profile']!='luna-bedrock-global' or c['endpoint']!='amazon-bedrock/global' or
     Path(b['path']).resolve()!=Path(c['root'])/'capability.json'):
  raise GatewayLifecycleError('Remaining r2 preflight condition differs')
 runid=hashlib.sha256(c['root'].encode()).hexdigest()[:12]
 if c['projects']!={c['provider']:{r:f'pacbench-{runid}-{c["provider"]}-{r}' for r in ['driver','memory','judge']}}:
  raise GatewayLifecycleError('Remaining r2 project identity differs')
 return c

def admission(project,profile,body=None):
 c=load();s=json.loads(Path(c['slot_path']).read_text());schedule=frozen.frozen()[1]
 batch=next((b for b in schedule if b['id']==s.get('batch_id')),None)
 if (profile!=c['profile'] or project!=c['projects'][c['provider']]['memory'] or s.get('provider')!=c['provider'] or
     s.get('kind')!='ingest' or not batch or s.get('document_sha256')!=batch['document_sha256'] or
     frozen.sha(s['source_file'])!=batch['document_sha256']):
  raise GatewayLifecycleError('Only exact frozen native ingest is granted; answer/judge denied')
 return {'root':c['root'],'slot':s,'capability':json.loads(os.environ[ENV]),'projects':[project]}

def serial(calls,permit):return None # Each process has an exclusive arm lock and one append-only journal.
