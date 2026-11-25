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
AUTHORED_CHATS = 'authored-chats'
AUTHORED_PROTOCOL = 'honcho-seven-daily-authored-hermes-chats-v2'
CANARY_PROTOCOL = 'honcho-day-one-authored-chat-canary-v1'
CANARY_QUESTIONS = ('rsvp-before', 'cancellation-before', 'obligations-before', 'lunch-snapshot')


@lru_cache(maxsize=3)
def plan(layout=None, *, first_four=False):
    from .workflow_daily_documents import compile_daily_week

    if layout not in (None, AUTHORED_CHATS):
        raise ValueError('Unknown Honcho Week session layout')
    if type(first_four) is not bool or (first_four and layout != AUTHORED_CHATS):
        raise ValueError('The four-turn canary needs the authored chat layout')
    _, docs, turns, basis, manifest = compile_daily_week()
    if (len(docs),sum(len(d.source_ids) for d in docs),len(turns)) != (7,142,49):
        raise ValueError('Frozen Honcho Week coverage differs')
    if any(instant(a.at) >= instant(b.at) for a,b in zip(turns,turns[1:])):
        raise ValueError('Frozen Week turns are out of chronological order')
    schedule = [dict(index=i+1,id=t.id,at=t.at,authored_session=t.session,measure=t.measure,after=t.after,
                     prompt_sha256=sha256(t.prompt.encode()).hexdigest(),
                     ready_files=[d.filename for d in docs if instant(d.cutoff)<=instant(t.at)])
                for i,t in enumerate(turns)]
    protocol, session = ((AUTHORED_PROTOCOL, AUTHORED_CHATS) if layout == AUTHORED_CHATS
                         else (PROTOCOL, 'one continuing Hermes session'))
    if first_four:
        turns, schedule = turns[:4], schedule[:4]
        sessions = [turn.session for turn in turns]
        if (tuple(turn.id for turn in turns) != CANARY_QUESTIONS
                or sessions[0] != sessions[-1] or len(set(sessions)) != 3
                or any(row['ready_files'] != [docs[0].filename] for row in schedule)
                or len(docs[0].source_ids) != 39):
            raise ValueError('The frozen day-one source or four-turn chat sequence changed')
        docs, manifest = gate.canonical_inputs(first_release=True)
        protocol = CANARY_PROTOCOL
    return docs,turns,dict(protocol=protocol,session=session,
                          corpus=basis,document_index_sha256=manifest['normalized_index_sha256'],
                          gradable_turns=sum(bool(t.claims) for t in turns),
                          criteria=sum(len(t.claims) for t in turns),turns=schedule)


def plan_for(scope):
    return plan(scope.get('week_session_layout'), first_four=scope.get('week_first_four', False))


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
    if accepted != [path_for(root,i,'verified') for i in range(len(accepted))] or len(accepted)>len(plan_for(scope)[1]):
        raise OwnershipConflict('Honcho Week has a changed turn prefix')
    return len(accepted)


def pending(scope):
    index = position(scope)
    _, turns, schedule = plan_for(scope)
    if index == len(turns):
        raise OwnershipConflict('Every frozen Honcho question is already complete')
    return index,turns[index],schedule['turns'][index]


def release_scope(scope,at):
    _, _, scheduled = pending(scope)
    expected = [entry['at'] for entry in scope['plan'] if instant(entry['at']) <= instant(scheduled['at'])]
    if at not in expected:
        raise OwnershipConflict('Honcho release lies after the next frozen question')


def previous_turns(scope, index):
    turns = plan_for(scope)[1]
    if scope.get('week_session_layout') != AUTHORED_CHATS:
        return list(enumerate(turns[:index]))
    return [(i, t) for i, t in enumerate(turns[:index]) if t.session == turns[index].session]


def _verified_binding(scope, index):
    root = Path(scope['root'])
    bound = binding(path_for(root, index, 'verified'))
    row = read(bound)
    if (row['index'], row['question']) != (index + 1, plan_for(scope)[1][index].id):
        raise OwnershipConflict('Honcho prior surface belongs to a different question')
    return bound, row


def _chat_binding(scope, index):
    earlier = previous_turns(scope, index)
    return _verified_binding(scope, earlier[-1][0]) if earlier else (None, None)


def prior_surface(scope, index):
    _, row = _chat_binding(scope, index)
    return read(row['surface']) if row else None


def validate_session(scope, index, session_id):
    """A new logical chat must receive a fresh physical session in the same account."""
    if scope.get('week_session_layout') != AUTHORED_CHATS:
        return
    native_ids, logical_ids = {}, {}
    turns = plan_for(scope)[1]
    for position, _ in enumerate(turns[:index]):
        _, row = _verified_binding(scope, position)
        user_chat, actual = row['authored_session'], row['session_id']
        if (turns[position].session != user_chat or not actual
                or native_ids.setdefault(actual, user_chat) != user_chat
                or logical_ids.setdefault(user_chat, actual) != actual):
            raise OwnershipConflict('Honcho sealed prefix has a changed chat binding')
    expected = logical_ids.get(turns[index].session)
    if (not isinstance(session_id, str) or not session_id or
            (session_id != expected if expected else session_id in native_ids)):
        raise OwnershipConflict('Honcho substituted or reused a native session for the authored chat')


def ready_bindings(root,expected_count):
    return [binding(root/f'honcho-daily-{i}-ready.json') for i in range(1, expected_count+1)]


def arm(scope,owner,turn,session_id):
    from .workflow_native import trace_position

    root = Path(scope['root'])
    index, expected, scheduled = pending(scope)
    chat_prior, row = _chat_binding(scope, index)
    prior = read(row['surface']) if row else None
    previous = _verified_binding(scope, index - 1) if index else (None, None)
    if (process_identity()!=scope['controller'] or expected!=turn
            or session_id!=(prior['session_id'] if prior else None)
            or gate.followup_ready(root,turn.id)!=turn.prompt):
        raise OwnershipConflict('Cannot arm a changed Honcho Week question or source prefix')
    witness = owner.witness
    gateway = process_identity(witness['pid'])
    if previous[1] and read(previous[1]['surface'])['gateway_identity'] != [gateway['pid'], gateway['start_ticks']]:
        raise OwnershipConflict('Honcho Week gateway changed during the conversation')
    if not index and owner.fence()['sessions']:
        raise OwnershipConflict('Honcho Week opening gateway is not empty')
    intent = dict(condition=gate.CONDITION, controller=scope['controller'], gateway=gateway,
        witness={key: witness[key] for key in ('pid', 'start_time', 'process_adoption')},
        ready=ready_bindings(root, len(scheduled['ready_files'])), question=turn.id, index=index,
        session_id=session_id, prior=previous[0], cursor=trace_position(root/'reuse-context.jsonl'))
    if scope.get('week_session_layout') == AUTHORED_CHATS:
        intent.update(authored_session=turn.session, chat_prior=chat_prior)
    return gate._write(path_for(root,index,'intent'), intent)


def active(scope):
    root = Path(scope['root'])
    index, turn, scheduled = pending(scope)
    path = path_for(root,index,'intent')
    owner = read(binding(path))
    prior = binding(path_for(root,index-1,'verified')) if index else None
    if owner['index']!=index or owner['prior']!=prior or owner['question']!=turn.id:
        raise OwnershipConflict('Honcho Week intent does not match the next frozen question')
    if scope.get('week_session_layout') == AUTHORED_CHATS:
        expected, row = _chat_binding(scope, index)
        if (owner.get('authored_session') != turn.session or owner.get('chat_prior') != expected
                or owner['session_id'] != (row['session_id'] if row else None)):
            raise OwnershipConflict('Honcho Week intent differs from the authored chat predecessor')
    return turn,owner,scheduled['ready_files'],index


def capture_session_name(name):
    if not isinstance(name,str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}',name):
        raise OwnershipConflict('Invalid stock Honcho chat session name')
    return name


def publish(scope,index,surface):
    root = Path(scope['root'])
    if index!=position(scope) or process_identity()!=scope['controller']:
        raise OwnershipConflict('Only the active Honcho Week turn can be committed')
    turn = plan_for(scope)[1][index]
    validate_session(scope, index, surface['session_id'])
    intent = binding(path_for(root,index,'intent'))
    public = gate._write(path_for(root,index,'surface.private'),surface)
    verified = dict(index=index+1, question=turn.id, intent=intent, surface=public)
    if scope.get('week_session_layout') == AUTHORED_CHATS:
        verified.update(authored_session=turn.session, session_id=surface['session_id'])
    return gate._write(path_for(root,index,'verified'), verified)
