"""Render or interpret development-only paired judge requests from the released Pizza Week."""
import argparse
from pacbench.judging import spans as spans
import json
from pathlib import Path

from pacbench.corpus.sources import load_bundle
from .format_review import DECODER
from pacbench.campaigns.pizza_week import MANIFEST, ROOT, checked_file
from .paired import (BASELINE, PROTOCOL, Source, UnavailableAnswer, assertions, build_pair, fields, fingerprint,
                             interpret, text_hash)
from ..workflows.runner import judge_options
from pacbench.campaigns.pizza_week import compile_week
from ..workflows.spec import instant

CONTRACT = ROOT / 'datasets/pizza-week/judge-experiments/question-contract-v1.json'
DATA = ROOT / 'datasets/pizza-week/r2'


def _json(path):
    return DECODER.decode(path.read_text(encoding='utf-8').strip())


class Week:
    def __init__(self, *, candidate=PROTOCOL):
        self.candidate = candidate
        spec, documents, self.turns, _ = compile_week('continuing-account')
        self.by_id = {t.id: t for t in self.turns}
        manifest = json.loads(checked_file(DATA, 'manifest.json', MANIFEST))
        bundle = load_bundle(DATA / manifest['source_bundle'], manifest['source_bundle_hash'])
        released = {key: doc.cutoff for doc in documents for key in doc.source_ids}
        self.documents = documents
        self.sources = {}
        for life in bundle['lives'].values():
            for row in life['messages']:
                key = row['source_id']
                self.sources[key] = Source(key, row['sender'], row['thread'], row['written_at'],
                                           released[key], row['text'], row['arrived_at'], row.get('available_at'))
        contract_text = CONTRACT.read_bytes().decode('utf-8')
        contract = DECODER.decode(contract_text.strip())
        fields(contract, ('protocol', 'schedule_sha256', 'turns'))
        if (contract['protocol'] != PROTOCOL or contract['schedule_sha256'] != fingerprint(spec)
                or not isinstance(contract['turns'], dict) or not contract['turns']
                or list(contract['turns']) != [turn.id for turn in self.turns if turn.claims]):
            raise ValueError('unrecognized protocol, schedule or incomplete ordered question coverage')
        self.entries = contract['turns']
        self.contract_sha256 = text_hash(contract_text)
        self.dataset_sha256 = manifest['dataset_sha256']

    def _correction(self, turn):
        return Source('turn/' + turn.id, 'user', turn.session, turn.at, turn.at, turn.prompt)

    def _authored_sources(self):
        authored = dict(self.sources)
        for turn in self.turns:
            if turn.measure == 'user-correction':
                source = self._correction(turn)
                authored[source.source_id] = source
        return authored

    def audit_projection(self, documents):
        """Check excerpts by source ID in the actual projection a trusted reader verified."""
        rendered = {}
        for doc in documents:
            for ident, body in zip(doc.source_ids, doc.bodies, strict=True):
                if ident in rendered or ident not in self.sources:
                    raise ValueError('duplicate or foreign projected source')
                original = self.sources[ident]
                if instant(doc.cutoff) != instant(original.released_at):
                    raise ValueError('projection release clock differs from the judge source clock')
                rendered[ident] = (body, doc.cutoff)
        if rendered.keys() != self.sources.keys():
            raise ValueError('incomplete Week source projection')
        evidence_uses = correction_uses = anchor_uses = 0
        cited = set()
        horizons = {}
        corrections = {}
        for turn in self.turns:
            cutoff = instant(turn.at)
            available = sorted(ident for ident, (_, release) in rendered.items() if instant(release) <= cutoff)
            horizons[turn.id] = dict(records=len(available), ids_sha256=fingerprint(available))
            if not turn.claims:
                if turn.measure != 'user-correction':
                    raise ValueError('unexpected ungraded Week turn')
                corrections['turn/' + turn.id] = self._correction(turn)
                continue
            entry = self.entries[turn.id]
            for claim, row in zip(turn.claims, entry['criteria'], strict=True):
                for ident in claim.evidence:
                    evidence_uses += 1
                    cited.add(ident)
                    if ident.startswith('turn/'):
                        if ident not in corrections or instant(corrections[ident].released_at) >= cutoff:
                            raise ValueError('undelivered or future authored correction')
                        correction_uses += 1
                    elif ident not in available:
                        raise ValueError('criterion evidence unavailable at question cutoff')
                for ref in row['anchors']:
                    ident, quote = ref['source_id'], ref['quote']
                    if ident in corrections:
                        body = corrections[ident].body
                    else:
                        body, _ = rendered[ident]
                    if body.count(quote) != 1:
                        raise ValueError('selected anchor differs in the supplied source projection')
                    anchor_uses += 1
        return dict(source_records=len(rendered), evidence_uses=evidence_uses,
                    distinct_evidence_keys=len(cited), correction_uses=correction_uses,
                    anchor_uses=anchor_uses, question_horizons=horizons,
                    projected_records_sha256=fingerprint(sorted(
                        (key, release, text_hash(body)) for key, (body, release) in rendered.items())))

    def inventory(self):
        authored = self._authored_sources()
        counts = {turn.id: len(assertions(turn, self.entries[turn.id], authored)[0])
                  for turn in self.turns if turn.claims}
        return dict(protocol=self.candidate, dataset_sha256=self.dataset_sha256,
                    contract_file_sha256=self.contract_sha256, migrated=counts, pending=[],
                    total_criteria=sum(counts.values()), ungraded=[t.id for t in self.turns if not t.claims],
                    frozen_evidence=self.audit_projection(self.documents))

    def from_surface(self, ident, surface, delivered_corrections, *, previous_turns=None):
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
                                 prior_public_dialogue=history, delivered_corrections=delivered_corrections),
                            previous_turns=previous_turns)

    def prepare(self, case, *, previous_turns=None):
        fields(case, ('turn_id', 'answer_state', 'answer', 'prior_public_dialogue', 'delivered_corrections'))
        ident = case['turn_id']
        if not isinstance(ident, str) or ident not in self.entries:
            raise ValueError('no complete authored contract for this question')
        if case['answer_state'] in ('failed', 'missing'):
            raise UnavailableAnswer('provider answer ' + case['answer_state'])
        if case['answer_state'] != 'received':
            raise ValueError('unknown answer state')
        turn = self.by_id[ident]
        earlier = self.turns[:self.turns.index(turn)]
        logical = [t for t in earlier if t.session == turn.session]
        previous = logical if previous_turns is None else list(previous_turns)
        if previous != logical and previous != earlier:
            raise ValueError('native prefix differs from both supported complete session layouts')
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
        return build_pair(turn, self.entries[ident], sources, case['answer'], history, candidate=self.candidate)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    inv = commands.add_parser('inventory', help='validate all migrated assertions and enumerate omissions')
    plan = commands.add_parser('plan', help='print paired requests, without dispatch')
    plan.add_argument('case', type=Path)
    score = commands.add_parser('interpret', help='score a supplied local reply against its request')
    score.add_argument('case', type=Path)
    score.add_argument('arm', choices=(BASELINE, PROTOCOL, spans.PROTOCOL))
    score.add_argument('reply', type=Path)
    for sub in (inv, plan, score):
        sub.add_argument('--candidate', choices=(PROTOCOL, spans.PROTOCOL), default=PROTOCOL)
    args = parser.parse_args(argv)
    week = Week(candidate=args.candidate)
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
