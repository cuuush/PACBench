"""Shared storage preserves exact model requests and fails on altered evidence."""
import json

import pytest

from pacbench.evidence_store import STORE,read_json,share_text
from pacbench.followups import save


def test_shared_histories_round_trip_exact_unicode_and_different_prefixes(tmp_path):
    history=''.join(f'{i}: café — Friday plans, then ordinary chat\r\n' for i in range(3000))
    chunks={}
    originals=[{'request':{'messages':[{'role':'user','content':prefix+history}]}}
               for prefix in ['First question\n','Different second question\n']]
    for i,value in enumerate(originals):save(tmp_path/f'{i}.json',share_text(value,chunks))
    save(tmp_path/STORE,{'version':1,'chunks':chunks})
    for i,original in enumerate(originals):assert read_json(tmp_path/f'{i}.json')==original
    assert sum(len(v) for v in chunks.values())<len(history)*1.2


@pytest.mark.parametrize('damage',['missing','changed','wrong-text-hash','invalid-key','store-symlink'])
def test_bad_shared_evidence_is_rejected(tmp_path,damage):
    chunks={};value=share_text('hello world\n'*1000,chunks)
    save(tmp_path/'record.json',value)
    if damage=='changed':chunks[next(iter(chunks))]='altered'
    if damage=='wrong-text-hash':value['sha256']='0'*64;save(tmp_path/'record.json',value)
    if damage=='invalid-key':value['$pacbench_text']=['../private'];save(tmp_path/'record.json',value)
    if damage!='missing':save(tmp_path/STORE,{'version':1,'chunks':chunks})
    if damage=='store-symlink':
        (tmp_path/STORE).rename(tmp_path/'other.json');(tmp_path/STORE).symlink_to(tmp_path/'other.json')
    with pytest.raises(ValueError):read_json(tmp_path/'record.json')


def test_plain_receipt_and_missing_store_are_not_required_for_small_text(tmp_path):
    value={'question':'ordinary small text','request':{'messages':[]}}
    save(tmp_path/'record.json',value)
    assert read_json(tmp_path/'record.json')==value


def test_compact_contracts_preserves_hashes_and_native_reuse(tmp_path):
    from pacbench.evidence_store import compact_contracts
    from pacbench.followups import digest
    from pacbench.semantic_contract import load_contract
    from pacbench.native_pilot import copy_prepared_files
    import shutil
    root=tmp_path/'preparation';root.mkdir()
    body={'claims':[{'id':'time','statement':'Friday at seven','evidence':['m1']}],
        'question':'When is dinner?','evidence_hash':'a'*64,
        'combined_review_receipt':{'prompt':'café — exact source history\r\n'*2000}}
    body['contract_hash']=digest(body)
    for path in (root/'contract.json',root/'evaluations/one/contract.json',root/'evaluations/two/contract.json'):
        save(path,body)
    for name in ('bundle.json','resolved-spec.json'):save(root/name,{})
    before=sum(p.stat().st_size for p in root.rglob('*.json'))
    compact_contracts(root)
    assert sum(p.stat().st_size for p in root.rglob('*.json')) < before*.5
    for path in root.rglob('contract.json'):
        assert read_json(path)==body
        assert load_contract(path).contract_hash==body['contract_hash']
    compact_contracts(root)  # Repeating compaction must keep all references valid.
    output=tmp_path/'run';output.mkdir()
    copy_prepared_files(root,output)
    shutil.copytree(root/'evaluations',output/'evaluations')
    assert read_json(output/'evaluations/one/contract.json')==body
    assert load_contract(output/'contract.json').contract_hash==body['contract_hash']
