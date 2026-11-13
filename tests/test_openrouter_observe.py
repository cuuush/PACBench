import json
from contextlib import contextmanager
from urllib.parse import urlsplit
from pacbench import openrouter_observe as observe
from pacbench.openrouter_route import MODEL


def test_generation_observation_is_read_only_free_and_identity_bound(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','private-unit-secret');calls=[]
    @contextmanager
    def opened(request,**kwargs):
        from io import StringIO
        url=request if isinstance(request,str) else request.full_url;calls.append(url)
        if url.endswith('/models'):payload={'data':[{'id':MODEL,'canonical_slug':'canonical'}]}
        else:payload={'data':{'id':'gen-one','model':'canonical','provider_name':'DeepInfra','native_tokens_reasoning':9}}
        yield StringIO(json.dumps(payload))
    monkeypatch.setattr(observe,'urlopen',opened)
    r=observe.collect([{'id':'row','upstream_request_id':'gen-one'},{'id':'unknown'}],tmp_path,interval=0)
    assert r['paid_model_calls']==r['ledger_mutations']==0
    assert r['items']['row']['state']=='observed' and r['items']['unknown']['state']=='missing-generation-id'
    assert all(urlsplit(u).path in ['/api/v1/models','/api/v1/generation'] for u in calls)
    assert 'private-unit-secret' not in (tmp_path/'index.json').read_text()
    assert (tmp_path/'index.json').stat().st_mode & 0o777==0o600
