"""Validate frozen preparation without running a model or a memory provider."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from pacbench.corpus.sources import load_bundle, messages_for, parse_json_object
from pacbench.corpus.contract import EvaluationContract, load_contract
from pacbench.followups import load_questions
from pacbench.evidence_store import read_json


def audit_combined_contract_review(root, document, observations, check):
    """Bind v7's two independent subreviews to their one actual model request."""
    if (document.get('authoring_version') not in {'event-scoped-v7','event-scoped-v8-attribute-anchors'} or
            document.get('kind', 'recall') == 'behavior'):
        return []
    from pacbench.corpus.contract import (ContractClaim, _dialogue, combined_review_prompt,
                                    contract_review_prompts, shared_context_review_prompt)
    from pacbench.corpus.contract import review_observations
    observations=review_observations(root,document,observations,check)
    claims = tuple(ContractClaim(c['id'], c['statement'], tuple(c['evidence'])) for c in document['claims'])
    claim_prompt, question_prompt = contract_review_prompts(
        claims=claims, author_facts=tuple(check.get('facts', [])), dialogue=_dialogue(observations),
        question_at=check['at'], user_question=check['prompt'], test_intent=check.get('expect') or check['prompt'],review_version=document['authoring_version'])
    retry = ('\nThe previous response was not a valid JSON object. '
             'Return one complete JSON object with no surrounding text.')
    receipt = document.get('combined_review_receipt') or {}
    try:
        prompt_format = receipt.get('prompt_format')
        if prompt_format is None and 'prompt_format' not in receipt:
            expected = combined_review_prompt(claim_prompt, question_prompt)
        elif prompt_format == 'shared-context-v2':
            expected = shared_context_review_prompt(
                claims=claims, author_facts=tuple(check.get('facts', [])), dialogue=_dialogue(observations),
                question_at=check['at'], user_question=check['prompt'], test_intent=check.get('expect') or check['prompt'],review_version=document['authoring_version'])
        else:
            raise ValueError('unknown combined review prompt format')
        path = receipt['model_call']
        if not isinstance(path, str) or not path:
            raise ValueError('missing model receipt')
        call = read_json(root / 'model-calls' / Path(path).name)
        response = json.loads(receipt['response'])
        review, question = response['claim_review'], response['question_review']
        if (document.get('question_source') != 'supplied' or
                receipt['prompt'] not in (expected, expected + retry) or
                call.get('status') != 'ok' or call.get('text') != receipt['response'] or
                call.get('request', {}).get('messages') != [{'role':'user','content':receipt['prompt']}] or
                review != document.get('review') or question != document.get('question_review') or
                review.get('complete') is not True or
                not isinstance(review.get('reviews'), list) or len(review['reviews']) != len(claims) or
                any(not isinstance(r,dict) or r.get('id') != c.id or
                    any(r.get(k) is not True for k in ('supported','relevant','unambiguous')) or
                    not isinstance(r.get('reason'),str) or not r['reason'].strip()
                    for r,c in zip(review['reviews'],claims)) or
                any(question.get(k) is not True for k in ('safe','faithful','elicits_all')) or
                not isinstance(question.get('reason'),str) or not question['reason'].strip()):
            raise ValueError('shared review does not match frozen inputs and passing subreviews')
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return [f"{check['id']}: combined contract review invalid: {exc}"]
    return []


def audit_scenes(root, resolved, life_id, messages):
    """Bind frozen message bodies to the actual saved passing checker input."""
    if not resolved.get('scenes'):return []  # Older minimal portable corpora.
    from dataclasses import fields
    from pacbench.corpus.authoring import ScenePlan,build_scene_observations
    from pacbench.followups import digest
    names={f.name for f in fields(ScenePlan)}
    rows=json.loads((root/'scenes.json').read_text())['scenes']
    if [r['scene_id'] for r in rows]!=[s['id'] for s in resolved['scenes']]:
        return ['Source realization list differs from resolved scene order']
    expected=[];errors=[]
    for row,spec in zip(rows,resolved['scenes']):
        plan=ScenePlan(**{k:v for k,v in spec.items() if k in names})
        built=[o.public() for o in build_scene_observations(life_id,plan,row['lines'])]
        expected.extend(built)
        if plan.source_review == 'author-source-v1':
            from pacbench.corpus.authoring import realize_scene
            from pacbench.corpus.prepare import realization_binding
            exact=realize_scene(plan,None,None)
            if (row['source_ids']!=[m['source_id'] for m in built] or
                    row['record']!=vars(exact.record) or row['verdict']!=exact.verdict or
                    row.get('verification_binding')!=realization_binding(plan,row['lines'],())):
                errors.append(f'{plan.id}: invalid unjudged author-source integrity/provenance')
            continue  # Deliberately no model verdict or checker receipt to validate.
        if row['source_ids']!=[m['source_id'] for m in built] or row['record']['status']!='ok' or row['verdict'].get('supported') is not True:
            errors.append(f"{plan.id}: incomplete source verification")
        if plan.messages is not None:
            record = row['record']
            if (record.get('source_mode') != 'supplied' or record.get('attempts') != 0 or
                    record.get('generation_paths')):
                errors.append(f'{plan.id}: supplied source has invalid writer provenance')
            if not row.get('verification_binding'):
                errors.append(f'{plan.id}: supplied source is missing its checker binding')
        if not row.get('verification_binding'):continue  # Legacy receipts keep their original protocol.
        paths=row['record'].get('judgment_paths',[])
        review=read_json(root/'scene-verifications'/Path(paths[-1]).name) if paths else {}
        binding=digest({'version':review.get('spec',{}).get('judge_prompt_version'),
                        'prompt':review.get('judge_prompt')})
        if (review.get('passed') is not True or review.get('spec')!=spec or
                review.get('dialogue')!=[f'{s}: {t}' for s,t in row['lines']] or
                review.get('verdict')!=row['verdict'] or binding!=row['verification_binding']):
            errors.append(f'{plan.id}: source differs from its passing checker input')
        if review.get('model_call'):
            call=read_json(root/'model-calls'/Path(review['model_call']).name)
            if (call.get('status')!='ok' or call.get('text')!=review.get('judge_response') or
                    call.get('request',{}).get('messages')!=[{'role':'user','content':review.get('judge_prompt')}]):
                errors.append(f'{plan.id}: checker receipt differs from actual model call')
        else:errors.append(f'{plan.id}: missing actual checker model receipt')
    if expected!=messages:errors.append('Frozen source bodies or metadata differ from verified scenes')
    return errors


@dataclass(frozen=True)
class PreparedCorpus:
    """Verified source and question evidence shared by plan, recording and native evaluation."""

    root: Path
    messages: list[dict]
    contract: EvaluationContract
    resolved: dict
    summary: dict
    input_hash: str
    spec_sha256: str | None
    evaluation_plan: dict | None
    evaluations: list
    corpus_hash: str | None = None

    @property
    def questions(self):
        return self.evaluations or [({'id': 'final', 'at': self.resolved['question_at']}, self.contract, None)]

    @property
    def source_basis(self):
        basis = {key: self.summary[key] for key in ('bundle_hash', 'contract_hash')}
        if self.corpus_hash is not None:
            basis['corpus_hash'] = self.corpus_hash
        if self.evaluation_plan is not None:
            basis['evaluation_plan_hash'] = self.evaluation_plan['plan_hash']
        return basis


def _inspect_prepared(root: Path):
    errors = []
    corpus = None
    try:
        summary = json.loads((root / 'summary.json').read_text())
        if summary['state'] != 'prepared' or summary['results']:
            errors.append('Preparation must not execute provider arms')
        bundle = load_bundle(root / 'bundle.json', summary['bundle_hash'])
        if len(bundle['lives']) != 1:
            errors.append('Expected one source life')
        messages = messages_for(bundle, next(iter(bundle['lives'])))
        ids = [m['source_id'] for m in messages]
        if not ids or len(ids) != len(set(ids)):
            errors.append('Missing or duplicate source IDs')
        evidence_hash = hashlib.sha256(json.dumps(messages,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        contract = load_contract(root / 'contract.json')
        has_checks=(root/'evaluation-plan.json').exists()
        if contract.contract_hash != summary['contract_hash'] or not has_checks and contract.evidence_hash != evidence_hash:
            errors.append('Frozen contract differs from source messages or summary')
        if not has_checks and any(not c.evidence or set(c.evidence) - set(ids) for c in contract.claims):
            errors.append('Claim cites unavailable source messages')
        load_questions(root / 'followup-plan.json', contract)
        resolved = json.loads((root / 'resolved-spec.json').read_text())
        errors.extend(audit_scenes(root,resolved,next(iter(bundle['lives'])),messages))
        if summary.get('corpus_review_version') and not (root/'realism-review.json').exists():
            errors.append('Required corpus realism review is missing')
        if (root/'realism-review.json').exists():
            from pacbench.followups import digest
            quality=json.loads((root/'realism-review.json').read_text())
            if summary.get('corpus_review_version') and quality.get('review_version') != summary['corpus_review_version']:
                errors.append('Corpus realism review version differs from preparation')
            from pacbench.corpus.review import CHECKPOINT_VERSION, BOUND_CHECKPOINT_VERSION, audit_checkpoints
            if quality.get('review_version') in {CHECKPOINT_VERSION,BOUND_CHECKPOINT_VERSION}:
                errors.extend(audit_checkpoints(root,messages,resolved,quality))
            else:
                intent={'scenes':[{k:s[k] for k in ('id','at','thread','scene','facts')} for s in resolved['scenes']],
                        'checks':resolved['checks']}
                if (quality.get('state')!='pass' or quality.get('source_hash')!=digest(messages) or
                        quality.get('intent_hash')!=digest(intent) or quality.get('review',{}).get('issues')):
                    errors.append('Corpus realism review is stale or unresolved')
                if (quality.get('review_version') in ('source-realism-v7','source-realism-v8','source-realism-v9','source-realism-v10','source-realism-v11') and
                        quality.get('scope') != 'source-coherence-and-public-question-answerability'):
                    errors.append('Corpus realism review scope is missing or incorrect')
                from pacbench.corpus.review import verdict_passes
                if quality.get('review_version') in {'source-realism-v10','source-realism-v11'}:
                    from pacbench.corpus.review import eligible_evidence
                    expected_bindings={c['id']:digest(eligible_evidence(messages,resolved,c['id'])) for c in resolved['checks']}
                    if quality.get('eligibility_bindings')!=expected_bindings:
                        errors.append('Corpus review eligible-history bindings differ from actual checkpoints')
                if not verdict_passes(quality.get('review'), messages, resolved, version=quality.get('review_version')):
                    errors.append('Corpus realism review lacks valid source-grounded assessments')
                if quality.get('review_version') in ('source-realism-v7','source-realism-v8','source-realism-v9','source-realism-v10','source-realism-v11') and 'validation_errors' in quality:
                    from pacbench.corpus.review import validation_errors
                    if quality['validation_errors'] != validation_errors(quality.get('review'), messages, resolved,
                                                                        version=quality['review_version']):
                        errors.append('Corpus realism validation errors differ from review evidence')
                call=read_json(root/'model-calls'/Path(quality.get('model_call','')).name)
                execution=quality.get('execution_condition')
                if execution is not None:
                    deadline=call.get('client_wall_deadline_s',call.get('direct_timing',{}).get('wall_deadline_s'))
                    if (execution.get('version')!='corpus-review-real-deadline-v1' or
                            type(deadline) is not int or not 0<deadline<=900 or
                            execution.get('wall_deadline_s')!=deadline):
                        errors.append('Corpus review client deadline differs from its actual model receipt')
                    if 'max_attempts' in execution and (execution['max_attempts']!=call.get('client_max_attempts') or
                            execution['max_attempts'] not in (1,2) or deadline==900 and execution['max_attempts']!=1):
                        errors.append('Corpus review attempt limit differs from its actual model receipt')
                if call.get('status')!='ok' or json.loads(call.get('text',''))!=quality.get('review'):
                    errors.append('Corpus realism review differs from its actual model receipt')
                from pacbench.corpus.review import SUPPORTED_VERSIONS, review_prompt
                if quality.get('review_version') in SUPPORTED_VERSIONS and call.get('request', {}).get('messages') != [
                        {'role': 'user', 'content': review_prompt(messages, resolved,version=quality['review_version'])}]:
                    errors.append('Corpus realism review did not receive the actual frozen source and expectations')
        if resolved.get('ingestion_days') is not None:
            from datetime import date, datetime
            from pacbench.document_ingestion import daily_batches
            batches=daily_batches(messages,resolved.get('timezone','America/New_York'))
            expected=resolved['ingestion_days']
            days=[date.fromisoformat(day) for day,_ in batches]
            if (len(days)!=expected or not days or (days[-1]-days[0]).days+1!=expected):
                errors.append('Frozen messages do not cover the requested contiguous ingestion days')
            latest=max(datetime.fromisoformat(m['collected_at']) for m in messages)
            if any(datetime.fromisoformat(c['at'])<=latest for c in resolved.get('checks',[])):
                errors.append('Daily-ingestion trial asks questions before all sources arrive')
        # Re-validate clocks and bounded questions, independent of provider runs.
        from pacbench.corpus.evaluation import resolve_checks
        normalized = resolve_checks(resolved, resolved['question_at'], resolved.get('timezone', ''))
        if normalized['checks'] != resolved.get('checks', []):
            errors.append('Evaluation checks differ from normalized source spec')
        plan, evaluations = None, []
        if has_checks:
            from pacbench.corpus.evaluation import load_evaluations

            def audit_review(document, evidence, check):
                errors.extend(audit_combined_contract_review(root, document, evidence, check))

            plan, evaluations = load_evaluations(root, messages, resolved, audit_review=audit_review)
            if plan['plan_hash'] != summary.get('evaluation_plan_hash'):
                errors.append('Evaluation plan differs from summary')
            if plan['checks'][-1]['contract_hash']!=contract.contract_hash:
                errors.append('Root contract differs from final checkpoint')
        if not errors:
            authored = root / 'spec.json'
            corpus = PreparedCorpus(root, messages, contract, resolved, summary, evidence_hash,
                                    hashlib.sha256(authored.read_bytes()).hexdigest() if authored.is_file() else None,
                                    plan, evaluations)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, AttributeError, IndexError) as exc:
        errors.append(f'{type(exc).__name__}: {exc}')
    return {'state': 'pass' if not errors else 'fail', 'errors': errors,
            'scope': 'frozen preparation integrity; no provider quality claim'}, corpus


def audit_prepared(root: Path) -> dict:
    report, _ = _inspect_prepared(root)
    return report


class PreparedCorpusError(ValueError):
    """The supplied corpus cannot be safely used for a run."""


def _materialize_week(source, root):
    from pacbench.followups import digest
    source, root = Path(source), Path(root)
    try:
        manifest = source.read_bytes()
        body = parse_json_object(manifest)
    except (OSError, ValueError) as exc:
        raise PreparedCorpusError(f'Cannot read frozen corpus manifest {source}: {exc}') from exc
    claimed = body.pop('corpus_hash', None)
    if not isinstance(claimed, str) or not isinstance(body.get('files'), dict):
        raise PreparedCorpusError('Expected a frozen corpus manifest. Run `python -m pacbench generate SPEC --output CORPUS` first.')
    if body.get('version') not in (1, 2) or digest(body) != claimed:
        raise PreparedCorpusError('Frozen week hash/version mismatch')
    files={}
    for name,value in body['files'].items():
        path=Path(name)
        if (path.is_absolute() or '..' in path.parts or path.suffix!='.json'
                or name != path.as_posix() or name == 'week.json'):
            raise PreparedCorpusError('Unsafe frozen week path')
        if body['version']==2:
            original=source.parent/path
            if not original.resolve().is_relative_to(source.parent.resolve()):
                raise PreparedCorpusError('Unsafe frozen week path')
            try:
                raw = original.read_bytes()
            except OSError as exc:
                raise PreparedCorpusError(f'Cannot read frozen corpus file {name}: {exc.strerror}') from exc
            if hashlib.sha256(raw).hexdigest() != value:
                raise PreparedCorpusError('Frozen week file hash mismatch: ' + name)
            try:
                json.loads(raw)
            except ValueError as exc:
                raise PreparedCorpusError(f'Invalid JSON in frozen corpus file {name}: {exc}') from exc
            files[path]=raw
        else:
            files[path]=(json.dumps(value,indent=2,ensure_ascii=False)+'\n').encode()
    # Check every reference before creating a destination or starting paid work.
    root.mkdir(parents=True,exist_ok=False)
    for path,raw in files.items():
        destination=root/path;destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(raw)
    return root,claimed,manifest


@contextmanager
def open_prepared(source, *, portable=False, destination=None, max_messages=None, retain_manifest=True):
    """Authenticate an entire portable corpus, or audit a historical diagnostic directory."""

    source = Path(source).resolve(strict=True)
    manifest = source / 'week.json' if source.is_dir() and portable else source

    def verify(root, corpus_hash):
        report, corpus = _inspect_prepared(root)
        if corpus is None:
            prefix = 'Frozen week failed preparation audit' if corpus_hash else 'Preparation failed integrity audit'
            raise PreparedCorpusError(prefix+': '+str(report['errors']))
        if max_messages is not None and len(corpus.messages) > max_messages:
            raise PreparedCorpusError(f'message budget exceeded: {len(corpus.messages)} / {max_messages}')
        return replace(corpus, corpus_hash=corpus_hash)

    def materialize(root):
        root, corpus_hash, manifest_bytes = _materialize_week(manifest, root)
        corpus = verify(root, corpus_hash)
        if retain_manifest:
            (root / 'week.json').write_bytes(manifest_bytes)
        return corpus

    if source.is_dir() and not portable:
        yield verify(source, None)
    elif destination is not None:
        yield materialize(destination)
    else:
        with TemporaryDirectory(prefix='pacbench-corpus-') as work:
            yield materialize(Path(work) / 'preparation')


def main() -> int:
    parser=argparse.ArgumentParser();parser.add_argument('root',type=Path)
    result=audit_prepared(parser.parse_args().root)
    print(json.dumps(result,indent=2))
    return int(result['state']!='pass')


if __name__=='__main__':raise SystemExit(main())
