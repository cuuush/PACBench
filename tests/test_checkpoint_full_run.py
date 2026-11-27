"""Full r2 scope remains distinct from the already-qualified canary."""

import pacbench.evidence_store as evidence_store

import copy, hashlib, json

import pytest

from pacbench.judging import retained_scope as cap

from pacbench.gateway_reuse import atomic

from pacbench.hermes_gateway import GatewayLifecycleError

def capability(tmp_path):
 projects={p:{r:f'pacbench-{hashlib.sha256(str(tmp_path/p).encode()).hexdigest()[:12]}-{p}-{r}' for r in ['memory','driver','judge']} for p in cap.PROVIDERS}
 binding=cap.create(tmp_path,projects,{p:'config' for p in cap.PROVIDERS});return binding,cap.load_full_scope(binding)

@pytest.mark.usefixtures('historical_checkpoint_archive')
def test_full_scope_exact_denominators_cross_day_and_no_canary_expansion(tmp_path,monkeypatch):
 from pacbench.judging import retained_scope as canary_capability
 binding,c=capability(tmp_path)
 _,schedule,checks,plan=cap.frozen_full()
 assert len(checks)==42 and sum(len(c.claims) for _,c,_ in checks)==188
 assert sum(len(b['source_ids']) for b in schedule)==142
 assert [len(g['questions']) for g in plan['groups']]==[6,8,5,4,2,17]
 rows={r['id']:r for r,_,_ in checks}
 assert rows['lunch-snapshot']['session']==rows['lunch-refreshed']['session']!=rows['lunch-fresh']['session']
 assert len(canary_capability.frozen_canary()[2])==3
 monkeypatch.setenv(cap.ENV,json.dumps(binding))
 with pytest.raises(GatewayLifecycleError):canary_capability.load(binding)
 for field in ['full_frozen','memcal','endpoint','model','scoring']:
  bad=copy.deepcopy(c);bad['specification'][field]='wrong';atomic(tmp_path/'capability.json',bad)
  with pytest.raises(GatewayLifecycleError):cap.load_full_scope(evidence_store.file_binding(tmp_path/'capability.json'))
 atomic(tmp_path/'capability.json',c)
 for other in ['PACBENCH_CHECKPOINT_CANARY','PACBENCH_EXECUTION_SCOPE','PACBENCH_NATIVE_DIAGNOSTIC_SCOPE']:
  with monkeypatch.context() as m:
   m.setenv(other,'not-the-current-scope')
   with pytest.raises(GatewayLifecycleError):cap.load_full_scope(binding)

@pytest.mark.usefixtures('historical_checkpoint_archive')
def test_recovery_ancestry_rechecks_all_bound_roots(tmp_path):
 first=tmp_path/'first';first.mkdir();b,c=capability(first)
 atomic(first/'report.json',{'frozen_inputs':{'sha256':cap.FROZEN}})
 second=tmp_path/'second';second.mkdir()
 projects={p:{r:f'pacbench-{hashlib.sha256(str(second/p).encode()).hexdigest()[:12]}-{p}-{r}' for r in ['memory','driver','judge']} for p in cap.PROVIDERS}
 b2=cap.create(second,projects,c['configs'],prior_root=first);c2=cap.read_full_binding(b2)
 assert cap.recovery_origins(c2)==[str(second),str(first)]
 atomic(first/'report.json',{'frozen_inputs':{'sha256':'changed'}})
 with pytest.raises(GatewayLifecycleError,match='evidence changed'):cap.recovery_origins(c2)

def test_recovered_prefix_resolves_sources_from_verified_ancestry(tmp_path,monkeypatch):
 from pacbench.judging.retained_week import source_proof
 prior=tmp_path/'prior';ancestor=tmp_path/'ancestor'
 old=ancestor/'mem0/checkpoint-1/source-proof.json';old.parent.mkdir(parents=True);old.write_text('{}')
 prior.mkdir()
 capability = prior / 'capability.json'
 capability.write_text('{}')
 def load(bound):
  assert bound['path'] == str(capability)
  return 'verified'
 monkeypatch.setattr(cap, 'load_full_scope', load)
 def origins(value):
  assert value=='verified';return [str(prior),str(ancestor)]
 monkeypatch.setattr(cap,'recovery_origins',origins)
 assert source_proof(prior,'mem0',1)==old
 assert not source_proof(prior,'mem0',2).exists()
 def broken(bound):raise GatewayLifecycleError('Recovery source changed')
 monkeypatch.setattr(cap,'load_full_scope',broken)
 with pytest.raises(GatewayLifecycleError):source_proof(prior,'mem0',1)
