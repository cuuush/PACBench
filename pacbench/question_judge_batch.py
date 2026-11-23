"""Offline paired shadow judging from a read-only, SHA-pinned Week answer export."""
import argparse
from dataclasses import asdict
import hashlib
import json
from itertools import zip_longest
import os
from pathlib import Path
import sys
import tempfile


def new_private_file(destination, chunks):
    """Publish a complete private local artifact once, even with competing writers."""
    with tempfile.NamedTemporaryFile(dir=destination.parent) as tmp:
        for chunk in chunks:
            tmp.write(chunk)
        tmp.flush()
        os.fsync(tmp.fileno())
        os.link(tmp.name, destination)


def export_retained(args):
    """Run under the separately reviewed retained-reader checkout, without a transport."""
    from pacbench import cohort_regrade as reader

    expected = {reader.__file__: '8b4b19155fffe6c1fe5014484bd256b9b69ee1de4e3ac3cef766033ef591d80e',
                reader.retained.__file__: '50fff4fc4ccc3ed937697df3237026f314eaef6d6e7159ef04a3a7474d32a8b9'}
    if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != sha for p, sha in expected.items()):
        raise ValueError('retained reader differs from reviewed implementation')
    entries, sha = reader.discover(args.manifest)
    if sha != args.sha256:
        raise ValueError('cohort manifest digest differs')
    selected = [entry for entry in entries if entry.arm == args.arm]
    if len(selected) != 1:
        raise ValueError('select one declared retained arm')
    entry = selected[0]
    directory, data = reader._arm(entry)
    _, coverage = reader._make_items(entry, data, 'luna-context-v4-v1')
    if (coverage['completed_turns'], coverage['completed_criteria'], coverage['execution_sealed'],
        coverage['verified_pending_completion']) != (49, 192, True, []):
        raise ValueError('retained arm is incomplete')
    if args.output.resolve().is_relative_to(directory.parent if directory.name == 'workflow' else directory):
        raise ValueError('choose an export destination outside the retained run')
    layout = 'single' if data.binding.get('single_native_session') else 'logical'
    if any(not d.bodies or len(d.source_ids) != len(d.bodies) for d in data.documents):
        raise ValueError('retained reader lacks exact record body bindings')
    docs = [dict(filename=d.filename, sha256=d.sha256, cutoff=d.cutoff, source_ids=list(d.source_ids), bodies=list(d.bodies))
            for d in data.documents]
    origin = dict(projected_sources_sha256=reader.digest(docs), arm=entry.arm, provider=entry.provider,
                  cohort_manifest_sha256=sha, entry_manifest_sha256=entry.sha256,
                  session_layout=layout, condition=data.condition, binding=data.binding,
                  source_provenance_sha256=reader.digest(data.source_provenance))

    def lines():
        for index, turn in enumerate(data.turns):
            surface, _ = data.answers[turn.id]
            row = dict(turn=asdict(turn), answer=surface['answer'], native_session=surface['session_id'],
                       conversation=[dict(role=r['role'], content=r['content']) for r in surface['history']
                                     if r['role'] in ('user','assistant') and r.get('content')],
                       conversation_times=surface['conversation_times'],
                       retained_origin=origin)
            if index == 0:
                row['retained_documents'] = docs
            yield (json.dumps(row, ensure_ascii=False) + '\n').encode()

    new_private_file(args.output, lines())
    return dict(arm=args.arm, session_layout=layout, export_sha256=file_sha(args.output), turns=49)


def file_sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def prepare(export, expected_sha, layout, *, candidate=None):
    from pacbench.judge_format_review import DECODER
    from pacbench.question_judge import canonical, fingerprint, nonblank
    from pacbench.workflow_stream import fingerprint as schedule_fingerprint
    from types import SimpleNamespace
    from pacbench.question_judge_week import Week

    week = Week() if candidate is None else Week(candidate=candidate)
    sessions, logical_bindings, corrections, pairs = {}, {}, {}, []
    provenance = None
    projection = {'state': 'offline-unverified-export'}
    # Hash and parse the very same bytes; do not trust a later reopening of the export.
    digest = hashlib.sha256()
    missing = object()
    with export.open('rb') as stream:
        for turn, raw in zip_longest(week.turns, stream, fillvalue=missing):
            if turn is missing or raw is missing:
                raise ValueError('expected the complete 49-turn exported schedule')
            digest.update(raw)
            if not raw.endswith(b'\n'):
                raise ValueError('torn answer export')
            row = DECODER.decode(raw.decode('utf-8').strip())
            if canonical(row['turn']) != canonical(asdict(turn)):
                raise ValueError('export order or frozen turn differs')
            origin = row.get('retained_origin')
            if turn == week.turns[0]:
                provenance = origin
                if origin is not None:
                    docs = row['retained_documents']
                    if (not isinstance(docs, list) or not docs
                            or fingerprint(docs) != origin['projected_sources_sha256']):
                        raise ValueError('retained source projection digest differs')
                    for key in ('cohort_manifest_sha256', 'entry_manifest_sha256'):
                        value = origin[key]
                        if (not isinstance(value, str) or len(value) != 64
                                or any(c not in '0123456789abcdef' for c in value)):
                            raise ValueError('missing retained manifest digest')
                    schedule = [{k: d[k] for k in ('filename', 'cutoff', 'sha256', 'source_ids')} for d in docs]
                    if schedule_fingerprint(schedule) != origin['binding']['document_schedule_sha256']:
                        raise ValueError('projected release clock or document differs from retained schedule')
                    if any(not d['bodies'] or len(d['bodies']) != len(d['source_ids']) for d in docs):
                        raise ValueError('retained reader lacks exact record body bindings')
                    projection = dict(state='verified', **week.audit_projection([SimpleNamespace(
                        cutoff=d['cutoff'], source_ids=d['source_ids'], bodies=d['bodies']) for d in docs]))
            elif 'retained_documents' in row:
                raise ValueError('duplicate retained projection')
            if origin is None and 'retained_documents' in row:
                raise ValueError('source projection without retained origin')
            if origin != provenance or origin is not None and origin['session_layout'] != layout:
                raise ValueError('retained origin or session layout changed')
            sid = row['native_session']
            chat = 'single' if layout == 'single' else turn.session
            if not nonblank(sid) or logical_bindings.setdefault(chat, sid) != sid:
                raise ValueError('native session changed during a chat')
            bound, earlier, prefix = sessions.setdefault(sid, (chat, [], []))
            if bound != chat:
                raise ValueError('native session shared across logical chats')
            history, answer = row['conversation'], row['answer']
            if not isinstance(history, list) or history[:len(prefix)] != prefix:
                raise ValueError('native history diverged from its previous turn')
            delta = history[len(prefix):]
            if (len(delta) < 2 or delta[0].get('role') != 'user' or delta[0].get('content') != turn.prompt
                    or any(r.get('role') not in ('assistant', 'tool') for r in delta[1:])
                    or delta[-1].get('role') != 'assistant' or delta[-1].get('content') != answer
                    or not nonblank(answer) or row['conversation_times'] != [t.at for t in earlier] + [turn.at]):
                raise ValueError('native question, final answer or timestamp differs')
            if turn.measure == 'user-correction':
                corrections['turn/' + turn.id] = dict(at=turn.at, session=turn.session, content=delta[0]['content'])
            if turn.claims:
                keys = {key for claim in turn.claims for key in claim.evidence if key.startswith('turn/')}
                delivery = {key: corrections[key] for key in keys}
                surface = dict(history=history, answer=answer, question_at=turn.at,
                               conversation_times=row['conversation_times'])
                pairs.append(week.from_surface(turn.id, surface, delivery, previous_turns=earlier))
            sessions[sid] = (chat, [*earlier, turn], history)
    if digest.hexdigest() != expected_sha:
        raise ValueError('answer export digest differs')
    if (len(week.turns), len(pairs), sum(len(p.rules) for p in pairs), len(corrections)) != (49,47,192,2):
        raise ValueError('incomplete frozen Week coverage')
    manifest = dict(protocol='question-scoped-week-paired-offline-v1', export_sha256=expected_sha,
                    session_layout=layout, native_sessions=len(sessions), retained_origin=provenance,
                    projection_audit=projection,
                    dataset_sha256=week.dataset_sha256, contract_sha256=week.contract_sha256,
                    turns=49, questions=47, criteria_per_arm=192, requests=94,
                    corrections_sha256=fingerprint(corrections), pairs=[pair.manifest() for pair in pairs])
    return manifest, pairs


def batch(args):
    from pacbench.judge_format_review import DECODER
    from pacbench.question_judge import BASELINE, PROTOCOL, fields, fingerprint, interpret

    from pacbench.question_judge_spans import PROTOCOL as span_protocol
    candidate = getattr(args, 'candidate', None) or PROTOCOL
    if candidate not in (PROTOCOL, span_protocol):
        raise ValueError('Unknown versioned candidate')
    manifest, pairs = prepare(args.export, args.sha256, args.session_layout, candidate=candidate)
    ordered = [(pair, arm) for i, pair in enumerate(pairs)
               for arm in ((BASELINE, candidate) if i % 2 == 0 else (candidate, BASELINE))]
    manifest['cell_order'] = [dict(turn=p.turn.id, arm=arm, request_sha256=fingerprint(p.requests[arm]))
                              for p, arm in ordered]
    plan_sha = fingerprint(manifest)
    if args.command == 'plan':
        result = dict(manifest=manifest, plan_sha256=plan_sha,
                      cells=[dict(turn=pair.turn.id, arm=arm, request=pair.requests[arm])
                             for pair, arm in ordered])
    else:
        replies = DECODER.decode(args.replies.read_text(encoding='utf-8').strip())
        fields(replies, ('plan_sha256', 'responses'))
        by_turn = replies['responses']
        if (replies['plan_sha256'] != plan_sha or not isinstance(by_turn, dict)
                or by_turn.keys() - {p.turn.id for p in pairs}):
            raise ValueError('reply collection outside the pinned plan')
        summaries = {arm: dict(planned=192, graded=0, credited=0) for arm in (BASELINE, candidate)}
        judgments = []
        for pair in pairs:
            cells = by_turn.get(pair.turn.id, {})
            if not isinstance(cells, dict) or cells.keys() - summaries.keys():
                raise ValueError('unexpected response arm')
            for arm in summaries:
                result = interpret(pair, arm, cells.get(arm))
                for key in ('graded', 'credited'):
                    summaries[arm][key] += result[key]
                judgments.append(result)
        result = dict(plan_sha256=plan_sha, source=manifest, arms=summaries, judgments=judgments,
                      response_set_sha256=fingerprint(replies), receipt_status='offline-unverified')
    new_private_file(args.output, [(json.dumps(result, ensure_ascii=False) + '\n').encode()])
    return dict(plan_sha256=plan_sha, turns=49, questions=47, planned_requests=94,
                **({'arms': summaries} if args.command == 'interpret' else {}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    native = commands.add_parser('export-retained', help='use the reviewed native reader on a sealed arm')
    native.add_argument('manifest', type=Path)
    native.add_argument('--arm', required=True)
    for cmd in ('plan', 'interpret'):
        sub = commands.add_parser(cmd)
        sub.add_argument('--candidate', help='versioned question-scoped judge protocol')
        sub.add_argument('export', type=Path)
        sub.add_argument('--session-layout', required=True, choices=('single','logical'))
        sub.add_argument('--sha256', required=True, help='expected private JSONL export digest')
        sub.add_argument('--output', required=True, type=Path)
        if cmd == 'interpret':
            sub.add_argument('--replies', required=True, type=Path)
    native.add_argument('--sha256', required=True, help='expected cohort manifest digest')
    native.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        summary = export_retained(args) if args.command == 'export-retained' else batch(args)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ImportError) as exc:
        print(f'Batch refused: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(summary))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
