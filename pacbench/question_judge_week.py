"""Render or interpret development-only paired judge requests from the released Pizza Week."""
import argparse
import json
from pathlib import Path

from .frozen import load_bundle
from .judge_format_review import DECODER
from .pizza_week_dataset import MANIFEST, ROOT, checked_file
from .question_judge import (BASELINE, PROTOCOL, Source, UnavailableAnswer, assertions, build_pair, fields, fingerprint,
                             interpret, text_hash)
from .workflow_runner import judge_options
from .workflow_spec import compile_week, instant

CONTRACT = ROOT / 'datasets/pizza-week/judge-experiments/question-contract-v1.json'
DATA = ROOT / 'datasets/pizza-week/r2'


def _json(path):
    return DECODER.decode(path.read_text(encoding='utf-8').strip())


class Week:
    def __init__(self):
        spec, documents, self.turns, _ = compile_week('continuing-account')
        self.by_id = {t.id: t for t in self.turns}
        manifest = json.loads(checked_file(DATA, 'manifest.json', MANIFEST))
        bundle = load_bundle(DATA / manifest['source_bundle'], manifest['source_bundle_hash'])
        released = {key: doc.cutoff for doc in documents for key in doc.source_ids}
        self.sources = {}
        for life in bundle['lives'].values():
            for row in life['messages']:
                key = row['source_id']
                self.sources[key] = Source(key, row['sender'], row['thread'], row['written_at'],
                                           released[key], row['text'], row['arrived_at'], row.get('available_at'))
        contract = _json(CONTRACT)
        fields(contract, ('protocol', 'schedule_sha256', 'turns'))
        if (contract['protocol'] != PROTOCOL or contract['schedule_sha256'] != fingerprint(spec)
                or not isinstance(contract['turns'], dict) or not contract['turns']
                or contract['turns'].keys() - self.by_id.keys()):
            raise ValueError('unrecognized protocol, schedule or migrated question')
        self.entries = contract['turns']
        self.contract_sha256 = text_hash(CONTRACT.read_text(encoding='utf-8'))
        self.dataset_sha256 = manifest['dataset_sha256']

    def _correction(self, turn):
        return Source('turn/' + turn.id, 'user', turn.session, turn.at, turn.at, turn.prompt)

    def inventory(self):
        authored = dict(self.sources)
        for turn in self.turns:
            if turn.measure == 'user-correction':
                source = self._correction(turn)
                authored[source.source_id] = source
        counts = {}
        pending = []
        for turn in self.turns:
            if turn.id in self.entries:
                rules, _ = assertions(turn, self.entries[turn.id], authored)
                counts[turn.id] = len(rules)
            elif turn.claims:
                pending.append(turn.id)
        return dict(protocol=PROTOCOL, dataset_sha256=self.dataset_sha256,
                    contract_file_sha256=self.contract_sha256, migrated=counts,
                    pending=pending, total_criteria=sum(len(t.claims) for t in self.turns))

    def from_surface(self, ident, surface, delivered_corrections):
        """Adapt a previously verified native surface. The caller owns native evidence verification."""
        turn = self.by_id[ident]
        rows = surface['history']
        users = [row for row in rows if row['role'] == 'user']
        if (not users or users[-1]['content'] != turn.prompt or surface['question_at'] != turn.at
                or rows[-1]['role'] != 'assistant' or surface['answer'] != rows[-1]['content']
                or len(surface['conversation_times']) != len(users) or surface['conversation_times'][-1] != turn.at):
            raise ValueError('saved answer surface differs from the frozen turn')
        history = judge_options(turn, surface)['conversation']
        return self.prepare(dict(turn_id=ident, answer_state='received', answer=surface['answer'],
                                 prior_public_dialogue=history, delivered_corrections=delivered_corrections))

    def prepare(self, case):
        fields(case, ('turn_id', 'answer_state', 'answer', 'prior_public_dialogue', 'delivered_corrections'))
        ident = case['turn_id']
        if not isinstance(ident, str) or ident not in self.entries:
            raise ValueError('no complete authored contract for this question')
        if case['answer_state'] in ('failed', 'missing'):
            raise UnavailableAnswer('provider answer ' + case['answer_state'])
        if case['answer_state'] != 'received':
            raise ValueError('unknown answer state')
        turn = self.by_id[ident]
        previous = [t for t in self.turns if t.session == turn.session and instant(t.at) < instant(turn.at)]
        history = case['prior_public_dialogue']
        if not isinstance(history, list):
            raise ValueError('prior public dialogue must be a list')
        users = []
        replied = True
        for row in history:
            fields(row, ('role', 'content', 'at'))
            if row['role'] == 'user':
                if not replied:
                    raise ValueError('prior dialogue lacks a received reply')
                users.append((row['content'], row['at']))
                replied = False
            elif row['role'] == 'assistant' and users and row['at'] == users[-1][1]:
                replied = True
            else:
                raise ValueError('foreign role or timestamp in prior public dialogue')
        if not replied or users != [(t.prompt, t.at) for t in previous]:
            raise ValueError('prior dialogue differs from the frozen session schedule')
        evidence = {k for c in turn.claims for k in c.evidence if k.startswith('turn/')}
        delivered = case['delivered_corrections']
        if not isinstance(delivered, dict) or set(delivered) != evidence:
            raise ValueError('missing or foreign correction delivery record')
        sources = dict(self.sources)
        for key, record in delivered.items():
            correction = self.by_id[key.removeprefix('turn/')]
            if (correction.measure != 'user-correction' or record != dict(
                    at=correction.at, session=correction.session, content=correction.prompt)):
                raise ValueError('correction delivery differs from original public user turn')
            sources[key] = self._correction(correction)
        return build_pair(turn, self.entries[ident], sources, case['answer'], history)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('inventory', help='validate all migrated assertions and enumerate omissions')
    plan = commands.add_parser('plan', help='print paired requests, without dispatch')
    plan.add_argument('case', type=Path)
    score = commands.add_parser('interpret', help='score a supplied local reply against its request')
    score.add_argument('case', type=Path)
    score.add_argument('arm', choices=(BASELINE, PROTOCOL))
    score.add_argument('reply', type=Path)
    args = parser.parse_args(argv)
    week = Week()
    if args.command == 'inventory':
        result = week.inventory()
    else:
        try:
            pair = week.prepare(_json(args.case))
        except UnavailableAnswer as exc:
            print(json.dumps(dict(state='provider_answer_unavailable', reason=str(exc))))
            return 2
        provenance = dict(**pair.manifest(), contract_file_sha256=week.contract_sha256,
                          dataset_sha256=week.dataset_sha256)
        if args.command == 'plan':
            result = dict(manifest=provenance, requests=pair.requests)
        else:
            result = dict(manifest=provenance, judgment=interpret(pair, args.arm, _json(args.reply)))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    raise SystemExit(main())
