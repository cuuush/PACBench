import json
from decimal import Decimal
import pytest
from pacbench.direct_accounting import DirectBudget, VERSION, request_cost_bound
from pacbench.shared_context_refinement import CONFIG, MODEL, ENDPOINT, refine


def test_refinement_preserves_unknowns_original_reservations_and_cap(tmp_path):
    path=tmp_path/'ledger.json'
    cfg={**CONFIG,'cap':'20','model':MODEL,'endpoint':ENDPOINT}
    path.write_text(json.dumps(dict(version=1,config=cfg,calls=[])))
    b=DirectBudget(path,profile='deepseek-flex')
    for i,cap in enumerate((393216,2000,393216)):
        b.reserve('synthetic'+str(i)+'-memory',{'model':MODEL,'messages':[],'max_tokens':cap})
    s=json.loads(path.read_text());s['calls'][0]['state']='uncertain';s['calls'][2]['state']='abandoned_unknown';path.write_text(json.dumps(s))
    original=json.loads(path.read_text());args=dict(proof_sha256='a'*64,authorization='Reviewed explicit shared-window correction')
    preview=refine(b,**args);assert len(preview['requests'])==1 and json.loads(path.read_text())==original
    event=refine(b,apply=True,**args);after=json.loads(path.read_text())
    assert event['bound_reduction_usd']=='0.0308281344'
    assert after['calls'][0]['state']=='uncertain' and after['calls'][0]['reserved']==original['calls'][0]['reserved']
    assert after['calls'][0]['accounted']=='0.1438646272'
    assert after['calls'][1:]==original['calls'][1:] and after['config']['cap']=='20'
    again=refine(b,apply=True,**args)
    assert again['bound_reduction_usd']=='0E-10' and len(json.loads(path.read_text())['unknown_bound_refinements'])==1
    # Later authoritative usage can still settle normally; a bound correction is
    # never represented as a paid receipt or zero-charge rejection.
    b.settle(after['calls'][0]['id'],200,json.dumps({'usage':{'cost':'.01','prompt_tokens':1,'completion_tokens':2}}).encode())
    assert json.loads(path.read_text())['calls'][0]['accounted']=='0.01'


def test_refinement_rejects_unreviewed_configuration_and_bad_proof(tmp_path):
    path=tmp_path/'ledger.json';cfg={**CONFIG,'cap':'20','model':MODEL,'endpoint':ENDPOINT,'context_tokens':100}
    path.write_text(json.dumps(dict(version=1,config=cfg,calls=[])));b=DirectBudget(path)
    with pytest.raises(ValueError,match='configuration'):refine(b,proof_sha256='a'*64,authorization='reviewed',apply=True)
    with pytest.raises(ValueError,match='proof'):refine(b,proof_sha256='not a hash',authorization='reviewed')
