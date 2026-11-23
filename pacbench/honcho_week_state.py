"""Immutable release and turn checkpoints for one stock Honcho Week."""
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import re

from . import honcho_daily_gate as gate
from .canary_capability import binding, read
from .dispatch_ownership import OwnershipConflict
from .live_dispatch import process_identity
from .workflow_spec import instant

PROTOCOL = 'honcho-seven-daily-single-hermes-week-v1'


@lru_cache(maxsize=1)
def plan():
    from .workflow_daily_documents import compile_daily_week

    _, docs, turns, basis, manifest = compile_daily_week()
    if (len(docs),sum(len(d.source_ids) for d in docs),len(turns)) != (7,142,49):
        raise ValueError('Frozen Honcho Week coverage differs')
    if any(instant(a.at) >= instant(b.at) for a,b in zip(turns,turns[1:])):
        raise ValueError('Frozen Week turns are out of chronological order')
    schedule = [dict(index=i+1,id=t.id,at=t.at,authored_session=t.session,measure=t.measure,after=t.after,
                     prompt_sha256=sha256(t.prompt.encode()).hexdigest(),
                     ready_files=[d.filename for d in docs if instant(d.cutoff)<=instant(t.at)])
                for i,t in enumerate(turns)]
    return docs,turns,dict(protocol=PROTOCOL,session='one continuing Hermes session',
                          corpus=basis,document_index_sha256=manifest['normalized_index_sha256'],
                          gradable_turns=sum(bool(t.claims) for t in turns),
                          criteria=sum(len(t.claims) for t in turns),turns=schedule)


def path_for(root,index,name):
    path = root/'week-turns'/f'{index+1:02d}-{name}.json'
    if path != path.resolve():
        raise OwnershipConflict('Foreign Honcho Week turn evidence')
    return path


def position(scope):
    root = Path(scope['root'])
    folder = root/'week-turns'
    if not folder.is_dir() or folder != folder.resolve():
        raise OwnershipConflict('Missing Honcho Week turn directory')
    accepted = sorted(folder.glob('*-verified.json'))
    if accepted != [path_for(root,i,'verified') for i in range(len(accepted))] or len(accepted)>len(plan()[1]):
        raise OwnershipConflict('Honcho Week has a changed turn prefix')
    return len(accepted)


def pending(scope):
    index = position(scope)
    _, turns, schedule = plan()
    if index == len(turns):
        raise OwnershipConflict('Every frozen Honcho question is already complete')
    return index,turns[index],schedule['turns'][index]


def release_scope(scope,at):
    _, _, scheduled = pending(scope)
    expected = [entry['at'] for entry in scope['plan'] if instant(entry['at']) <= instant(scheduled['at'])]
    if at not in expected:
        raise OwnershipConflict('Honcho release lies after the next frozen question')


def prior_surface(scope,index):
    if index == 0:
        return None
    root = Path(scope['root'])
    prior = read(binding(path_for(root,index-1,'verified')))
    if prior['index'] != index or prior['question'] != plan()[1][index-1].id:
        raise OwnershipConflict('Honcho prior surface belongs to a different question')
    return read(prior['surface'])


def ready_bindings(root,expected_count):
    return [binding(root/f'honcho-daily-{i}-ready.json') for i in range(1, expected_count+1)]


def arm(scope,owner,turn,session_id):
    from .workflow_native import trace_position

    root = Path(scope['root'])
    index, expected, scheduled = pending(scope)
    prior = prior_surface(scope,index)
    if (process_identity()!=scope['controller'] or expected!=turn
            or session_id!=(prior['session_id'] if prior else None)
            or gate.followup_ready(root,turn.id)!=turn.prompt):
        raise OwnershipConflict('Cannot arm a changed Honcho Week question or source prefix')
    witness = owner.witness
    gateway = process_identity(witness['pid'])
    if prior and prior['gateway_identity'] != [gateway['pid'],gateway['start_ticks']]:
        raise OwnershipConflict('Honcho Week gateway changed during the conversation')
    if not prior and owner.fence()['sessions']:
        raise OwnershipConflict('Honcho Week opening gateway is not empty')
    return gate._write(path_for(root,index,'intent'),dict(condition=gate.CONDITION,controller=scope['controller'],
        gateway=gateway,witness={key:witness[key] for key in ('pid','start_time','process_adoption')},
        ready=ready_bindings(root,len(scheduled['ready_files'])),question=turn.id,index=index,
        session_id=session_id,prior=binding(path_for(root,index-1,'verified')) if prior else None,
        cursor=trace_position(root/'reuse-context.jsonl')))


def active(scope):
    root = Path(scope['root'])
    index, turn, scheduled = pending(scope)
    path = path_for(root,index,'intent')
    owner = read(binding(path))
    prior = binding(path_for(root,index-1,'verified')) if index else None
    if owner['index']!=index or owner['prior']!=prior or owner['question']!=turn.id:
        raise OwnershipConflict('Honcho Week intent does not match the next frozen question')
    return turn,owner,scheduled['ready_files'],index


def capture_session_name(name):
    if not isinstance(name,str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}',name):
        raise OwnershipConflict('Invalid stock Honcho chat session name')
    return name


def publish(scope,index,surface):
    root = Path(scope['root'])
    if index!=position(scope) or process_identity()!=scope['controller']:
        raise OwnershipConflict('Only the active Honcho Week turn can be committed')
    intent = binding(path_for(root,index,'intent'))
    public = gate._write(path_for(root,index,'surface.private'),surface)
    return gate._write(path_for(root,index,'verified'),dict(index=index+1,question=plan()[1][index].id,
                                                            intent=intent,surface=public))
