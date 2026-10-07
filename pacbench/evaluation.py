"""Generic prompt/fact sequences, without event slots."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .followups import digest, freeze_policy, load_questions, save
from .semantic_contract import build_contract, load_contract


def resolve_checks(spec, question_at, timezone):
    checks = spec.get('checks')
    modes = {'evaluation_context': spec.get('evaluation_context', 'shared')}
    if modes['evaluation_context'] not in ('shared', 'isolated'):
        raise ValueError('context must be shared or isolated')
    if checks is None or checks == []:
        return {**modes, 'checks': []}
    if not isinstance(checks, list) or not 1 <= len(checks) <= 10000:
        raise ValueError('checks must contain 1–10000 evaluation turns')
    result, ids = [], set()
    previous = datetime.fromisoformat(question_at)
    for row in checks:
        if not isinstance(row, dict):
            raise ValueError('each check must be an object')
        cid, prompt, facts = row.get('id'), row.get('prompt'), row.get('facts',[])
        expect=row.get('expect')
        if (not isinstance(cid, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', cid) or cid in ids or
                not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 4000 or
                not isinstance(facts, list) or not (1 <= len(facts) <= 50 or facts==[] and isinstance(expect,str) and expect.strip()) or
                any(not isinstance(f, str) or not f.strip() for f in facts)):
            raise ValueError('check needs a unique safe id, prompt, and expected fact list')
        from .prepare import local_clock
        at = local_clock(row.get('at') or question_at, ZoneInfo(timezone) if timezone else None)
        if at.utcoffset() is None:
            raise ValueError('Evaluation clocks require a timezone')
        if at < previous:
            raise ValueError('evaluation turns must follow the first question in time order')
        previous = at
        extra={k:row[k] for k in ('session','kind','trigger','depends_on','expect') if k in row}
        if extra.get('kind','recall') not in ('recall','behavior'):raise ValueError('Unknown assistant turn kind')
        if 'session' in extra and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',extra['session']):raise ValueError('Unsafe session id')
        if extra.get('trigger','always') not in ('always','clarification'):raise ValueError('Unknown turn trigger')
        if 'depends_on' in extra and extra['depends_on'] not in ids:raise ValueError('Turn dependency must precede it')
        if extra.get('trigger')=='clarification' and 'depends_on' not in extra:raise ValueError('Conditional reply needs depends_on')
        ids.add(cid)
        result.append({'id': cid, 'prompt': prompt.strip(), 'facts': facts,
                       'at': at.isoformat(timespec='seconds'),**extra})
    return {**modes, 'checks': result}


def visible_messages(messages, at):
    clock = datetime.fromisoformat(at)
    return [m for m in messages if datetime.fromisoformat(m['collected_at']) <= clock]


def contract_evidence(messages, checks, current):
    available=visible_messages(messages,current['at'])
    if not any('session' in c or c.get('kind')=='behavior' for c in checks):return available
    # Planned public user instructions establish intended state, never an
    # invented successful assistant reply. This evidence is judge-only.
    for row in checks:
        available.append({'source_id':'assistant-user:'+row['id'],'written_at':row['at'],
            'sender':'Me','text':f"[assistant session {row.get('session','shared')}] {row['prompt']}"})
        if row['id']==current['id']:break
    return available


def freeze_evaluations(resolved, messages, target, model, root):
    rows,errors = [],[]
    for check in resolved['checks']:
        path = root / 'evaluations' / check['id']
        available = contract_evidence(messages,resolved['checks'],check)
        if not available:raise ValueError('Evaluation has no available source messages')
        try:
            contract = build_contract(scene='Available source dialogue', test_intent=check.get('expect') or check['prompt'],
                observations=available, author_facts=tuple(check['facts']), generate=model,
                verify=model, question_generate=model, output_path=path / 'contract.json',
                user_question=check['prompt'], question_at=check['at'],behavior=check.get('kind')=='behavior')
        except Exception as exc:
            errors.append({'id':check['id'],'reason':f'{type(exc).__name__}: {exc}'})
            save(root/'evaluation-errors.json',errors)
            continue
        policy = freeze_policy(contract, path / 'followup-plan.json')
        conditional={r['id'] for r in resolved['checks'] if r.get('trigger')=='clarification'}
        requires=sorted({e.split(':',1)[1] for c in contract.claims for e in c.evidence
                         if e.startswith('assistant-user:') and e.split(':',1)[1] in conditional and e!='assistant-user:'+check['id']})
        rows.append({'id': check['id'], 'at': check['at'], 'contract_hash': contract.contract_hash,
                     'policy_hash': policy['plan_hash'],**({'requires':requires} if requires else {}),
                     **{k:check[k] for k in ('session','kind','trigger','depends_on','expect') if k in check}})
    body = {'version': 1, 'context': resolved['evaluation_context'], 'checks': rows}
    if errors:body['errors']=errors
    plan = {**body, 'plan_hash': digest(body)}
    save(root / 'evaluation-plan.json', plan)
    return plan


def load_evaluations(root, messages, resolved):
    validated = resolve_checks(resolved, resolved['question_at'], resolved.get('timezone', ''))
    if validated['checks'] != resolved['checks']:
        raise ValueError('evaluation spec not normalized')
    plan = json.loads((root / 'evaluation-plan.json').read_text())
    if plan.get('version') != 1 or plan.get('plan_hash') != digest({k:v for k,v in plan.items() if k != 'plan_hash'}):
        raise ValueError('evaluation plan hash/version mismatch')
    if (plan['context'] != resolved['evaluation_context'] or
            [r['id'] for r in plan['checks']] != [r['id'] for r in resolved['checks']]):
        raise ValueError('evaluation plan differs from frozen spec')
    loaded = []
    for row, spec in zip(plan['checks'], resolved['checks']):
        directory = root / 'evaluations' / row['id']
        c = load_contract(directory / 'contract.json')
        policy = load_questions(directory / 'followup-plan.json', c)
        document = json.loads((directory / 'contract.json').read_text())
        evidence=contract_evidence(messages,resolved['checks'],spec)
        if (any(row.get(k)!=spec.get(k) for k in ('session','kind','trigger','depends_on','expect')) or
                c.contract_hash != row['contract_hash'] or c.evidence_hash != digest(evidence) or
                policy['plan_hash'] != row['policy_hash'] or row['at'] != spec['at'] or
                c.question != spec['prompt'] or spec['facts'] and [x.statement for x in c.claims] != spec['facts'] or
                spec.get('expect') and document.get('test_intent')!=spec['expect'] or
                document.get('question_at') != row['at'] or document.get('question_review', {}).get('elicits_all') is not True):
            raise ValueError('evaluation contract, prompt, facts, clock or review mismatch')
        visible = {m['source_id'] for m in evidence}
        if any(not c.evidence or any(e not in visible for e in c.evidence) for c in c.claims):
            raise ValueError('evaluation cites unavailable evidence')
        conditional={r['id'] for r in resolved['checks'] if r.get('trigger')=='clarification'}
        requires=sorted({e.split(':',1)[1] for claim in c.claims for e in claim.evidence
                         if e.startswith('assistant-user:') and e.split(':',1)[1] in conditional and e!='assistant-user:'+spec['id']})
        if row.get('requires',[])!=requires:raise ValueError('Conditional evidence dependency mismatch')
        loaded.append((row, c, policy))
    return plan, loaded



def prepare_existing(source, spec, output, model):
    """Freeze new questions against unchanged, previously verified dialogue."""
    import shutil
    from .native_pilot import read_prepared
    from .prepare import resolve_spec
    from .preparation_audit import audit_prepared
    messages, _, _ = read_prepared(source, 1200)
    if set(spec) - {'checks', 'question_at', 'evaluation_context'}:
        raise ValueError('recheck spec may change only questions, clocks and context modes')
    original = json.loads((source / 'spec.json').read_text())
    if 'days' in original:
        from .prepare import normalize_week
        original=normalize_week(original,json.loads((source/'resolved-spec.json').read_text())['week_start'])
    authored = {**original, **spec}
    for old in ('providers', 'prepare_only', 'exposure_context', 'exposure_delivery', 'maintenance_at'):
        authored.pop(old, None)
    _, target, resolved = resolve_spec(authored, output)
    if not resolved['checks']:
        raise ValueError('recheck requires checks')
    output.mkdir(parents=True, exist_ok=False)
    for name in ('bundle.json', 'bundle-provenance.json', 'scenes.json'):
        if (source / name).exists():
            shutil.copyfile(source / name, output / name)
    for name in ('scene-verifications', 'model-calls'):
        if (source / name).exists():
            shutil.copytree(source / name, output / name)
    save(output / 'spec.json', authored)
    save(output / 'resolved-spec.json', resolved)
    save(output / 'recheck-provenance.json', {'source': str(source.resolve()),
         'bundle_hash': json.loads((source / 'summary.json').read_text())['bundle_hash'],
         'original_contract_hash': json.loads((source / 'contract.json').read_text())['contract_hash']})
    plan = freeze_evaluations(resolved, messages, target, model, output)
    first = output / 'evaluations' / plan['checks'][-1]['id']
    for name in ('contract.json', 'followup-plan.json'):
        shutil.copyfile(first / name, output / name)
    summary = {'state': 'prepared', 'results': [], 'evaluation_plan_hash': plan['plan_hash'],
        'bundle_hash': json.loads((source / 'summary.json').read_text())['bundle_hash'],
        'contract_hash': plan['checks'][-1]['contract_hash']}
    save(output / 'summary.json', summary)
    report = audit_prepared(output)
    save(output / 'run-audit.json', report)
    if report['state'] != 'pass':
        raise ValueError('new evaluation failed audit: ' + str(report['errors']))
    return summary


def main():
    import argparse
    from .creds import load_driver
    from .gateway_client import client_settings, require_live_cost_boundary
    from .model_calls import ModelClient
    parser = argparse.ArgumentParser(description='Freeze new prompt/fact checks against existing verified dialogue.')
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require_live_cost_boundary()
    driver = load_driver()
    base, key = client_settings('pacbench-judge')
    judge = ModelClient(key=key, model=driver.model_id, url=base + '/chat/completions',
                        record_dir=args.output / 'model-calls')
    prepare_existing(args.prepared, json.loads(args.spec.read_text()), args.output, judge)
    print(args.output)


if __name__ == '__main__':
    main()
