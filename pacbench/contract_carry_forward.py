"""Versioned historical approval carry-forward for specifically authorized query edits.

No review is relabeled as current: original contracts and receipts remain intact.
"""
import hashlib,json
from pathlib import Path
from .followups import digest

VERSION='uncited-cross-session-query-equivalence-v1'
# Narrow to this explicitly authorized corrective wording; not a general rule
# that questions can never carry state. All four edits request information.
APPROVED={
 'rsvp-before':('Where are we with everyone’s replies for Friday lunch?', 'Who sent Friday’s lunch invitation, and what responses have come back so far?'),
 'withdrawal':('What’s the Friday lunch plan—who, when and where?', 'What’s the Friday lunch plan now—who is still agreed, when and where?'),
 'visit-final':('What arrival and departure times did Morgan and I settle on?', 'What arrival and departure dates and times did Morgan and I settle on?'),
 'hall-alias':('What do MR Hall and ZX Annex stand for?', 'What do the messages establish about the meanings of MR Hall and ZX Annex, and what remains unexplained?')}


def validate(witness,old_evidence,new_evidence,check,document,policy_bytes,model_bytes,corpus_hash):
    if witness.get('version')!=VERSION:raise ValueError('Unknown carry-forward witness version')
    if witness.get('status')!='carried-forward-equivalent-inputs':raise ValueError('Not a historical carry-forward')
    if witness['check']!=check:raise ValueError('Carry-forward current check changed')
    if witness['corpus_hash']!=corpus_hash:raise ValueError('Carry-forward source corpus changed')
    if witness['contract_hash']!=document['contract_hash']:raise ValueError('Carry-forward claims or contract changed')
    if witness['policy_sha256']!=hashlib.sha256(policy_bytes).hexdigest():raise ValueError('Carry-forward recovery policy changed')
    if witness['model_sha256']!=hashlib.sha256(model_bytes).hexdigest():raise ValueError('Carry-forward review model receipt changed')
    if digest(old_evidence)!=document['evidence_hash'] or witness['old_evidence_hash']!=digest(old_evidence) or witness['new_evidence_hash']!=digest(new_evidence):raise ValueError('Carry-forward evidence hash differs')
    if len(old_evidence)!=len(new_evidence):raise ValueError('Carry-forward evidence length changed')
    cited={e for c in document['claims'] for e in c['evidence']};changes=[]
    approved=witness['authorized_query_substitutions']
    for old,new in zip(old_evidence,new_evidence):
        if old==new:continue
        sid=old['source_id']
        if (sid in cited or old.get('thread')==check.get('session') or
            old.get('channel')!='assistant' or not sid.startswith('assistant-user:') or
            {k:v for k,v in old.items() if k!='text'}!={k:v for k,v in new.items() if k!='text'}):raise ValueError('Carry-forward changed substantive or cited evidence')
        pair=APPROVED.get(sid.removeprefix('assistant-user:'))
        prefix=f"[assistant session {old.get('thread')}] "
        if pair is None or (old['text'],new['text'])!=(prefix+pair[0],prefix+pair[1]):raise ValueError('Unapproved factual/query change')
        permitted=approved.get(sid)
        if (not permitted or not permitted.get('reason') or
            permitted['before']!=old['text'] or permitted['after']!=new['text']):raise ValueError('Unmatched authorized query substitution')
        changes.append({'source_id':sid,'before':old['text'],'after':new['text']})
    if changes!=witness['changes']:raise ValueError('Carry-forward diff differs')
    return old_evidence

def review_observations(root,document,new_evidence,check):
    path=Path(root)/'carry-forward'/f"{check['id']}.json"
    if not path.exists():return new_evidence
    witness=json.loads(path.read_text());model=document['combined_review_receipt']['model_call']
    bundle=json.loads((Path(root)/'bundle.json').read_text())
    calls=Path(root)/'contract-review-model-calls'
    if not calls.exists():calls=Path(root)/'model-calls'
    return validate(witness,witness['original_observations'],new_evidence,check,document,
        (Path(root)/'evaluations'/check['id']/'followup-plan.json').read_bytes(),
        (calls/Path(model).name).read_bytes(),bundle['bundle_hash'])


def copy_review_evidence(source, output):
    """Keep historical approvals auditable without attributing shared prep as arm spend."""
    import shutil
    source,output=Path(source),Path(output)
    if not (source/'carry-forward').exists():return
    destination=output/'carry-forward'
    if not destination.exists():shutil.copytree(source/'carry-forward',destination)
    calls=output/'contract-review-model-calls';calls.mkdir(exist_ok=True)
    for witness in (source/'carry-forward').glob('*.json'):
        doc=json.loads((source/'evaluations'/witness.stem/'contract.json').read_text())
        name=Path(doc['combined_review_receipt']['model_call']).name
        target=calls/name;raw=(source/'model-calls'/name).read_bytes()
        if target.exists() and target.read_bytes()!=raw:raise ValueError('Historical review receipt collision')
        if not target.exists():target.write_bytes(raw)
