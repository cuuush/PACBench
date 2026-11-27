"""Exact canary-only funding gates and the unmodified native launch path."""

import pacbench.evidence_store as evidence_store

import copy, hashlib, json

import pytest

from pacbench.judging import retained_scope as cap

from pacbench.gateway_reuse import atomic

from pacbench.hermes_gateway import GatewayLifecycleError

def capability(tmp_path):
 projects={p:{r:f'pacbench-{hashlib.sha256(str(tmp_path/p).encode()).hexdigest()[:12]}-{p}-{r}' for r in ['memory','driver']} for p in cap.PROVIDERS}
 b=cap.create(tmp_path,projects,{p:'config' for p in cap.PROVIDERS});c=cap.load(b)
 g=c['specification']['groups'][0];s={'kind':'ingest','provider':'mem0','group_id':g['id'],'batch_ids':g['ready_batch_ids'],'batch_id':g['ready_batch_ids'][-1]}
 atomic(tmp_path/'active-slot.json',s)
 return b,c,s

@pytest.mark.usefixtures('historical_checkpoint_archive')
def test_exact_frozen_slice_and_contract_tampering(tmp_path,monkeypatch):
 b,c,s=capability(tmp_path)
 assert [len(b['source_ids']) for b in c['specification']['schedule']]==[39,23]
 assert [q['id'] for g in c['specification']['groups'] for q in g['questions']]==list(cap.IDS)
 monkeypatch.setenv(cap.ENV,json.dumps(b))
 assert cap.admission(c['projects']['mem0']['memory'],'luna-bedrock-global')['slot']==s
 for project,profile in [('pacbench-judge','luna-bedrock-global'),(c['projects']['mem0']['driver'],'luna-bedrock-global'),(c['projects']['memcal']['memory'],'luna-bedrock-global'),(c['projects']['mem0']['memory'],'deepseek-flex')]:
  with pytest.raises(GatewayLifecycleError):cap.admission(project,profile)
 for field,value in [('full_frozen','bad'),('memcal','bad'),('model','wrong'),('endpoint','wrong')]:
  bad=copy.deepcopy(c);bad['specification'][field]=value;atomic(tmp_path/'capability.json',bad)
  with pytest.raises(GatewayLifecycleError):cap.load(evidence_store.file_binding(tmp_path/'capability.json'))
 atomic(tmp_path/'capability.json',c)
 for bad in [dict(s,batch_ids=s['batch_ids']+['2026-10-06']),dict(s,group_id='bad'),dict(s,kind='scored')]:
  atomic(tmp_path/'active-slot.json',bad)
  with pytest.raises(GatewayLifecycleError):cap.admission(c['projects']['mem0']['memory'],'luna-bedrock-global')
