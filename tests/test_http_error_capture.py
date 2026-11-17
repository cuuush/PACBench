"""Synthetic authenticated HTTPError reads; no network or billing."""
import io,json,hashlib,urllib.error,urllib.request
from datetime import datetime,timezone
from pathlib import Path
import pytest
from tests.test_single_admission_owner import owner,body
from pacbench import direct_http


def rejection():
 return json.dumps({'error':{'code':404,'message':'No endpoints found that can handle the requested parameters. To learn more about provider routing, visit: https://openrouter.ai/docs/guides/routing/provider-selection','metadata':{'failed_routing_step':'Filter by Parameters','routing_funnel':[{'step':'Filter by Allowed Providers','endpoint_count':1}]}}}).encode()


@pytest.mark.parametrize('complete',[True,False])
def test_urllib_http_error_preserves_body_and_only_read_completion_proves_eof(owner,monkeypatch,complete):
 b=owner.budget;r=b.reserve('pacbench-judge',body());raw=rejection()
 def original(self,request,data=None,timeout=None):raise urllib.error.HTTPError(request.full_url,404,'Not Found',{'Content-Type':'application/json'},io.BytesIO(raw))
 monkeypatch.setattr(urllib.request.OpenerDirector,'open',original)
 monkeypatch.setattr(direct_http,'_installed',False)
 monkeypatch.setattr(direct_http,'_install_httpx',lambda m:None)
 monkeypatch.setattr(direct_http,'admitted',lambda *a:{'headers':{},'body':b'{}','budget':b,'id':r['id']})
 direct_http.install()
 with pytest.raises(urllib.error.HTTPError) as caught:urllib.request.OpenerDirector().open(urllib.request.Request('https://openrouter.ai/api/v1/chat/completions',data=b'{}'))
 error=caught.value
 assert error.code==404 and error.read(0)==b'' and b.snapshot()[0]['state']=='reserved'
 if complete:assert error.read()==raw
 else:assert error.read(8)==raw[:8]
 error.close();row=b.snapshot()[0]
 assert row['state']=='uncertain' and row['response_body_complete'] is complete
 proof=row['private_error_body'];assert proof['body_complete'] is complete
 if complete:
  event=b.release_routing_rejection(r['id'],evidence_sha256=proof['sha256'])
  assert event['provider_reported_cost_usd'] is None
 else:
  with pytest.raises(ValueError):b.release_routing_rejection(r['id'],evidence_sha256=proof['sha256'])


def test_caller_recovery_is_exact_request_and_attempt_bound_without_rewriting_capture(owner,tmp_path):
 b=owner.budget;request={**body(),'temperature':0.0};r=b.reserve('pacbench-judge',request)
 b.settle(r['id'],404) # reproduce the old lost-body branch
 row=b.snapshot()[0];assert row['response_body_complete'] is False
 record={'created_at':datetime.now(timezone.utc).isoformat(),'client_max_attempts':1,'attempt':1,'status':'failed','error':'HTTP 404: Not Found',
  'url':'https://openrouter.ai/api/v1/chat/completions','model':b.model,'protocol':'chat','request':request,'response':None,'usage':{},'error_response':rejection().decode()}
 p=tmp_path/'record.json'
 for field,value in [('model','wrong'),('request',{**request,'messages':[]} ),('attempt',2),('error_response','x'*8000)]:
  p.write_text(json.dumps({**record,field:value}));sha=hashlib.sha256(p.read_bytes()).hexdigest()
  with pytest.raises(ValueError):b.attach_caller_routing_evidence(r['id'],p,record_sha256=sha)
  assert not b.snapshot()[0].get('private_error_body')
 p.write_text(json.dumps(record));sha=hashlib.sha256(p.read_bytes()).hexdigest()
 event=b.attach_caller_routing_evidence(r['id'],p,record_sha256=sha)
 assert event['releases_hold'] is False and b.snapshot()[0]['state']=='uncertain'
 assert b.snapshot()[0]['response_body_complete'] is False
 assert b.attach_caller_routing_evidence(r['id'],p,record_sha256=sha)==event
 proof=b.snapshot()[0]['private_error_body'];b.release_routing_rejection(r['id'],evidence_sha256=proof['sha256'])
 assert b.snapshot()[0]['state']=='rejected_no_dispatch'
