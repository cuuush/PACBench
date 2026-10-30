"""Prepare verified source messages and question contracts without provider execution."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .authoring import (ScenePlan, SceneRecord, SceneRealization, SceneJudgeError,
    build_scene_observations, realize_scene, scene_spec, judge_prompt, judge_scene)
from .creds import load_driver
from .gateway_client import client_settings
from .gateway_client import SpendingBlocked, ModelAccessBlocked
from .frozen import bundle_hash, write_bundle
from .model_calls import ModelClient, ResponsesWriter, public_record
from .semantic_contract import build_contract, load_contract
from .followups import freeze_policy, save
from .preparation_audit import audit_prepared

def local_clock(value: str, zone: ZoneInfo | None) -> datetime:
    """Interpret an offset-free authored clock as local wall time for its day."""
    clock = datetime.fromisoformat(value)
    if zone is None:
        return clock
    if clock.tzinfo is not None:
        wall = clock.replace(tzinfo=None)
        valid_offsets = {wall.replace(tzinfo=zone, fold=fold).utcoffset()
                         for fold in (0, 1)}
        if clock.utcoffset() not in valid_offsets:
            raise ValueError(f"offset conflicts with local time zone: {value}")
        converted = clock.astimezone(zone)
        if converted.replace(tzinfo=None) != wall:
            raise ValueError(f"nonexistent local time: {value}")
        return converted
    clock = clock.replace(tzinfo=zone)
    # ZoneInfo silently permits nonexistent spring-forward wall times.
    if clock.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != clock.replace(tzinfo=None):
        raise ValueError(f"nonexistent local time: {value}")
    if clock.replace(fold=0).utcoffset() != clock.replace(fold=1).utcoffset():
        raise ValueError(f"ambiguous local time: {value}")
    return clock

def resolve_spec(spec: dict, root: Path, *, week_start=None) -> tuple[list[ScenePlan], ScenePlan, dict]:
    """Fill operational clocks and identifiers without changing author prose."""
    spec = normalize_week(spec,week_start)
    rows = spec.get("scenes")
    if not isinstance(rows, list) or not rows:
        raise ValueError("experiment needs at least one scene")
    noise = spec.get('noise','low')
    if noise not in ('low','medium','high'):
        raise ValueError('noise must be low, medium or high')
    if "facts" in spec and (not isinstance(spec["facts"], list) or
                            not spec["facts"] or
                            any(not isinstance(f, str) or not f.strip()
                                for f in spec["facts"])):
        raise ValueError("top-level facts must be nonempty prose assertions")
    timezone_name = spec.get("timezone", "America/New_York")
    if timezone_name and not isinstance(timezone_name, str):
        raise ValueError("timezone must be an IANA time zone name")
    try:
        zone = ZoneInfo(timezone_name) if timezone_name else None
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown time zone: {timezone_name}") from exc
    seed = (local_clock(spec["question_at"], zone) - timedelta(days=1)
            if spec.get("question_at") else datetime.now(zone or timezone.utc))
    scenes = []
    counts = {}
    for row in rows:
        key=(row.get('channel','text'),row.get('thread',row.get('id','')),str(row.get('at',row.get('window_start','')))[:10])
        counts[key]=counts.get(key,0)+1
    for index, authored in enumerate(rows, 1):
        row = dict(authored)
        row['noise']=noise
        if 'message_range' not in row:
            if row.get('channel')=='email':row['message_range']=[2,6]
            elif noise=='low':row['message_range']=[2,12]
            else:
                if not row.get('participants'):raise ValueError('Chatty weeks require explicit chat participants')
                key=(row.get('channel','text'),row.get('thread',row.get('id','')),str(row.get('at',row.get('window_start','')))[:10])
                minimum,maximum=(20,40) if noise=='medium' else (600,800)
                row['messages_per_person']=[max(1,minimum//counts[key]),max(1,maximum//counts[key])]
                row['message_range']=[max(2,row['messages_per_person'][0]*len(row['participants'])),
                                      row['messages_per_person'][1]*len(row['participants'])]
                if noise=='high' and not row.get('window_end'):
                    # A noise level describes the whole day's conversation,
                    # not thousands of texts crammed into a 20 minute scene.
                    date=str(row.get('at',row.get('window_start','')))[:10]
                    if not date:raise ValueError('High noise needs an explicit source day')
                    row['window_end']=f'{date}T23:59:00'
                    # Respect authored clocks and the next scene in this chat.
                    start=str(row.get('at',row.get('window_start','')))
                    later=[r.get('at',r.get('window_start','')) for r in rows if
                        r.get('channel','text')==key[0] and r.get('thread',r.get('id',''))==key[1] and
                        str(r.get('at',r.get('window_start','')))[:10]==date and str(r.get('at',r.get('window_start','')))>start]
                    if later:row['window_end']=min(later)
        row.setdefault("id", f"scene-{index:02d}")
        if not row.get("at") and not row.get("window_start"):
            row["at"] = (seed.replace(hour=18, minute=0, second=0,
                                       microsecond=0) +
                         timedelta(minutes=21 * (index - 1))).isoformat(timespec="seconds")
        if zone:
            for field in ("at", "window_start", "window_end"):
                if row.get(field):
                    row[field] = local_clock(row[field], zone).isoformat(timespec="seconds")
            row["timezone"] = timezone_name
        scenes.append(ScenePlan(**row))
    if len({s.id for s in scenes}) != len(scenes):
        raise ValueError("scene IDs must be unique")
    for previous, current in zip(scenes, scenes[1:]):
        if datetime.fromisoformat(current.window_start) < datetime.fromisoformat(previous.window_start):
            raise ValueError("scenes must be authored in fictional time order")
    last_thread_end: dict[tuple[str, str], datetime] = {}
    for scene in scenes:
        key = (scene.channel, scene.thread)
        start = datetime.fromisoformat(scene.window_start)
        if key in last_thread_end and start < last_thread_end[key]:
            raise ValueError(f"overlapping windows in thread {scene.thread}")
        last_thread_end[key] = datetime.fromisoformat(scene.window_end)
    if spec.get("test_scene_id"):
        target = next((s for s in scenes if s.id == spec["test_scene_id"]), None)
        if target is None:
            raise ValueError("test_scene_id does not identify a scene")
    else:
        eligible = [s for s in scenes if s.facts or s.test_intent.strip()]
        if len(eligible) != 1:
            raise ValueError("choose one test scene when multiple scenes have facts or intent")
        target = eligible[0]
    if not target.facts and not target.test_intent.strip():
        raise ValueError("test scene needs required facts or test intent")
    life_id = spec.get("id") or root.name
    latest = max(datetime.fromisoformat(s.window_end) for s in scenes)
    next_day = latest.date() + timedelta(days=1)
    default_question = (datetime.combine(next_day, time(hour=9), zone)
                        if zone else (latest + timedelta(days=1)).replace(
                            hour=9, minute=0, second=0, microsecond=0))
    question_at = local_clock(spec["question_at"], zone) if spec.get("question_at") else default_question
    question_at = question_at.isoformat(timespec="seconds")
    if not spec.get("checks") and datetime.fromisoformat(question_at) <= latest:
        raise ValueError("evaluation must follow the source scenes")
    if any(key in spec for key in ('providers', 'prepare_only', 'exposure_context', 'exposure_delivery', 'week_of', 'days')):
        raise ValueError("authoring specs contain scenes and evaluation checks; select providers in native_pilot")
    from .evaluation import resolve_checks
    checks = resolve_checks(spec, question_at, timezone_name)
    if checks['checks'] and datetime.fromisoformat(checks['checks'][-1]['at']) <= latest:
        raise ValueError('Final evaluation must follow all source scenes')
    ingestion_days = spec.get('ingestion_days')
    if ingestion_days is not None:
        days = sorted({datetime.fromisoformat(s.at).date() for s in scenes})
        if (type(ingestion_days) is not int or ingestion_days < 1 or
                len(days) != ingestion_days or (days[-1] - days[0]).days + 1 != ingestion_days):
            raise ValueError('ingestion_days must match contiguous source days')
        if any(datetime.fromisoformat(c['at']) <= latest for c in checks['checks']):
            raise ValueError('Daily-ingestion trials ask questions only after all sources')
    return scenes, target, {
        "id": life_id, "test_scene_id": target.id,
        "timezone": timezone_name,
        "question_at": question_at, "scenes": [scene_spec(s) for s in scenes],
        "noise": noise, "week": bool(spec.get('week')),
        **({'ingestion_days': ingestion_days} if ingestion_days is not None else {}),
        **({"week_start":spec["week_start"]} if "week_start" in spec else {}), **checks}


WEEKDAYS=('monday','tuesday','wednesday','thursday','friday','saturday','sunday')


def clock_on_day(day, value):
    """Human wall clocks belong to their containing weekday."""
    import re
    if not isinstance(value,str):raise ValueError('Time must be human-readable, such as 7:30 am or 8 pm')
    clock=re.sub(r'\s+','',value.lower())
    for fmt in ('%I:%M%p','%I%p'):
        try:return datetime.combine(day,datetime.strptime(clock,fmt).time()).isoformat(timespec='seconds')
        except ValueError:pass
    raise ValueError(f'Invalid human time: {value!r}; use 7:30 am or 8 pm')


def normalize_week(spec, week_start=None):
    """Expand readable weekdays and reusable channels only at preparation time."""
    if 'days' not in spec and 'weeks' not in spec:return spec
    if 'days' in spec and 'weeks' in spec:raise ValueError('Use days or weeks, not both')
    if any(k in spec for k in ('scenes','checks','facts')):
        raise ValueError('Weeks use inline scene facts and turn expectations, without a top-level fact catalog')
    weeks=spec.get('weeks',[spec.get('days')]);channels=spec.get('channels')
    if not isinstance(weeks,list) or not weeks:raise ValueError('weeks must be a nonempty list of weekday maps')
    if any(not isinstance(days,dict) or set(days)!=set(WEEKDAYS) for days in weeks):
        raise ValueError('Week days must be monday through sunday')
    if not isinstance(channels,dict) or not channels:raise ValueError('Week needs reusable channel definitions')
    allowed={'type','participants','name','subject'}
    for key,value in channels.items():
        if not isinstance(value,dict) or set(value)-allowed or not value.get('type') or not value.get('participants'):
            raise ValueError(f'Channel {key!r} needs type and participants, with an optional group name')
    from datetime import date
    monday=date.fromisoformat(week_start) if week_start else datetime.now(ZoneInfo(spec.get('timezone','America/New_York'))).date()
    if week_start and monday.weekday()!=0:raise ValueError('week-start must be a Monday')
    monday-=timedelta(days=monday.weekday())
    scenes,checks=[],[]
    for offset in range(7*len(weeks)):
        week_index,weekday_index=divmod(offset,7)
        weekday=WEEKDAYS[weekday_index]
        day=weeks[week_index][weekday];date=monday+timedelta(days=offset)
        if not isinstance(day,dict) or set(day)-{'scenes','conversations'}:raise ValueError(f'Invalid {weekday} section')
        for scene in day.get('scenes',[]):
            row=dict(scene);channel_id=row.pop('channel',None)
            if 'weeks' in spec:row['id']=f"w{week_index+1}-{row['id']}"
            if channel_id not in channels:raise ValueError(f'Unknown channel {channel_id!r}')
            definition=channels[channel_id]
            row.update(channel=definition['type'],thread=channel_id,participants=definition['participants'],
                chat_name=definition.get('name',''),at=clock_on_day(date,row.pop('time')))
            if definition.get('subject'):
                row['email_subject']=definition['subject']
            if 'until' in row:row['window_end']=clock_on_day(date,row.pop('until'))
            if not isinstance(row.get('facts'),list) or any(not isinstance(f,str) or not f.strip() for f in row['facts']):
                raise ValueError('Scene facts are inline English statements')
            scenes.append(row)
        for conversation in day.get('conversations',[]):
            session=conversation['id'];clock=clock_on_day(date,conversation['time'])
            for index,turn in enumerate(conversation['turns']):
                row=dict(turn)
                if 'facts' in row or 'criteria' in row:raise ValueError('Assistant turns use an English expect description')
                if not isinstance(row.get('expect'),str) or not row['expect'].strip():raise ValueError('Assistant turn needs an English expect description')
                row['at']=clock_on_day(date,row.pop('time')) if 'time' in row else (datetime.fromisoformat(clock)+timedelta(minutes=2*index)).isoformat()
                row['session']=session
                checks.append(row)
    if not scenes or not checks:raise ValueError('Week requires sources and assistant turns')
    return {**{k:v for k,v in spec.items() if k not in ('days','weeks','channels')},'week':True,
        'week_start':monday.isoformat(),'scenes':scenes,'checks':checks,'question_at':checks[0]['at'],
        'test_scene_id':next(s['id'] for s in reversed(scenes) if s['facts'])}


def generation_spec(plan):
    """A checker revision can reverify text without paying to rewrite it."""
    return {k:v for k,v in scene_spec(plan).items() if k!='judge_prompt_version'}


def realization_binding(plan, lines, verification_lines):
    from .authoring import JUDGE_PROMPT_VERSION
    from .followups import digest
    return digest({'version':JUDGE_PROMPT_VERSION,
        'prompt':judge_prompt(plan,list(lines),verification_lines)})


def reuse_realization(plan, previous, judge, *, model, record_dir, verification_lines):
    """Recheck cached text when its actual evidence context or checker changes."""
    lines=tuple(tuple(line) for line in previous['lines'])
    record=SceneRecord(**previous['record'])
    if previous.get('verification_binding')==realization_binding(plan,lines,verification_lines):
        return SceneRealization(plan.id,lines,record,previous['verdict'])
    verdict,pc,rc,path=judge_scene(plan,list(lines),judge,model=model,record_dir=record_dir,
        attempt=record.judge_calls+1,prior_lines=verification_lines)
    record.judge_calls+=1;record.prompt_chars+=pc;record.response_chars+=rc
    if path:record.judgment_paths.append(path)
    return SceneRealization(plan.id,lines,record,verdict)


def load_scene_cache(root):
    path=root/'model-cache/scene-realizations.json'
    if path.exists():return json.loads(path.read_text())
    if not (root/'scenes.json').exists():return {}
    plans={p['id']:p for p in json.loads((root/'resolved-spec.json').read_text())['scenes']}
    return {r['scene_id']:{'spec':plans[r['scene_id']],'realization':r}
        for r in json.loads((root/'scenes.json').read_text())['scenes'] if r['scene_id'] in plans}


def _prepare_body(spec: dict, root: Path, progress: dict) -> dict:
    scenes, target, resolved = resolve_spec(spec, root,week_start=progress.get("week_start"))
    cache=load_scene_cache(root) if progress.get('resume') else {}
    reusable={sid:entry['realization'] for sid,entry in cache.items()
        if entry['realization']['record']['status']=='ok' and entry['realization']['verdict'].get('supported') is True and
        any(s.id==sid and generation_spec(s)=={k:v for k,v in entry['spec'].items()
            if k!='judge_prompt_version'} for s in scenes)}
    if progress.get('resume'):
        # Preserve *all* completed renders before publishing partial progress or
        # a revised spec. Interrupting a recheck must not erase later scenes.
        save(root/'model-cache/scene-realizations.json',cache)
        for name in ('evaluation-plan.json','evaluation-errors.json','contract.json','followup-plan.json','week.json','scene-errors.json'):
            (root/name).unlink(missing_ok=True)
    save(root / "resolved-spec.json", resolved)
    life_id = resolved["id"]
    progress["stage"] = "scene generation and verification"
    save(root / "experiment-status.json", progress)
    driver = load_driver()
    key = os.environ["META_API_KEY"]
    call_dir = root / "model-calls"
    writer = ResponsesWriter(key=key, model=driver.model_id, record_dir=call_dir,
                             url=client_settings("pacbench-authoring")[0] + "/responses",
                             cache_dir=root / "model-cache")
    judge = ModelClient(key=key, model=driver.model_id, record_dir=call_dir,
                        url=client_settings("pacbench-judge")[0] + "/chat/completions",
                        cache_dir=root / "model-cache")
    # Keep independent verification and answer grading unchanged. Native low
    # reasoning produced valid criterion drafts where the default setting
    # repeatedly emitted malformed JSON on the larger multi-outing question.
    draft = ModelClient(key=key, model=driver.model_id, record_dir=call_dir,
                        url=client_settings("pacbench-judge")[0] + "/chat/completions",
                        cache_dir=root / "model-cache", reasoning_effort="low")
    observations = []
    scene_records = []
    thread_history: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    failures, blocked_threads = [], set()
    for plan in scenes:
        key = (plan.channel, plan.thread)
        verification_lines=tuple((m['written_at'],m['channel']+':'+m['thread'],m['sender'],m['text'])
            for m in observations if datetime.fromisoformat(m['written_at'])<datetime.fromisoformat(plan.at))
        if key in blocked_threads:
            failures.append({'scene_id':plan.id,'state':'skipped','reason':'Earlier scene in this thread failed'})
            continue
        try:
            realized=None
            if plan.id in reusable:
                previous=reusable[plan.id]
                try:
                    realized=reuse_realization(plan,previous,judge,model=driver.model_id,
                        record_dir=root/'scene-verifications',verification_lines=verification_lines)
                except SceneJudgeError:
                    pass  # Preserve the rejected review; redraw only this inconsistent scene.
            if realized is None:
                realized = realize_scene(plan, writer, judge, model=driver.model_id,
                                     record_dir=root / "scene-verifications",
                                     prior_lines=tuple(thread_history.get(key, ())),
                                     verification_lines=verification_lines)
        except (SpendingBlocked,ModelAccessBlocked) as exc:
            failures.append({'scene_id':plan.id,'state':'ungraded','reason':str(exc)})
            save(root/'scene-errors.json',failures)
            break
        except Exception as exc:
            failures.append({'scene_id':plan.id,'state':'ungraded','reason':f'{type(exc).__name__}: {exc}'})
            blocked_threads.add(key)
            save(root/'scene-errors.json',failures)
            continue
        new_messages = build_scene_observations(life_id, plan, list(realized.lines))
        thread_history.setdefault(key, []).extend(
            (o.written_at, o.sender, o.text) for o in new_messages)
        observations.extend(o.public() for o in new_messages)
        row={"scene_id": plan.id, "lines": realized.lines,
                              "source_ids": [o.source_id for o in new_messages],
                              "record": vars(realized.record),
                              "verdict": realized.verdict,
                              'verification_binding':realization_binding(plan,realized.lines,verification_lines)}
        scene_records.append(row)
        cache[plan.id]={'spec':scene_spec(plan),'realization':row}
        save(root/'model-cache/scene-realizations.json',cache)
        save(root / "scenes.json", {"scenes": scene_records})
    bundle = {"bundle_version": "0.3.0", "lives": {life_id: {
        "lineage": life_id, "messages": observations}}}
    write_bundle(bundle, root / "bundle.json")
    save(root / "bundle-provenance.json", {"hash": bundle_hash(bundle),
                                           "source": "verified-scenes"})
    if failures:
        save(root/'scene-errors.json',failures)
        summary={'state':'incomplete','results':[],'message_count':len(observations),
                 'bundle_hash':bundle_hash(bundle),'errors':failures}
        save(root/'summary.json',summary)
        return summary
    progress["stage"] = "contract generation and review"
    save(root / "experiment-status.json", progress)
    evaluation_plan = None
    if resolved['checks']:
        from .evaluation import freeze_evaluations
        evaluation_plan = freeze_evaluations(resolved, observations, target, judge, root,
                                            resume=bool(progress.get('resume')),generate=draft)
        if evaluation_plan.get('errors'):
            summary={'state':'incomplete','results':[],'bundle_hash':bundle_hash(bundle),
                'message_count':len(observations),'errors':evaluation_plan['errors']}
            save(root/'summary.json',summary)
            return summary
        first = evaluation_plan['checks'][-1]['id']
        (root / 'contract.json').write_bytes((root / 'evaluations' / first / 'contract.json').read_bytes())
        contract = load_contract(root / 'contract.json')
    else:
        contract = build_contract(
            scene=target.scene, test_intent=target.test_intent,
            author_facts=tuple(spec.get("facts", target.facts)),
            observations=observations, generate=draft, verify=judge,
            question_generate=judge, output_path=root / "contract.json")
    progress["contract_frozen_at"] = datetime.now(timezone.utc).isoformat()
    progress['stage'] = 'corpus realism review'
    save(root / 'experiment-status.json', progress)
    from .corpus_review import VERSION as REVIEW_VERSION, review_corpus
    review_corpus(observations, resolved, judge, root)
    freeze_policy(contract, root / "followup-plan.json")
    summary = {"state": "prepared", "results": [], "message_count":len(observations),
               "noise":resolved['noise'],
               "corpus_review_version": REVIEW_VERSION,
               "contract_hash": contract.contract_hash, "bundle_hash": bundle_hash(bundle)}
    if evaluation_plan:
        summary['evaluation_plan_hash'] = evaluation_plan['plan_hash']
    save(root / "summary.json", summary)
    return summary


def export_week(root, destination):
    """Index existing JSON evidence once; copy the directory to reuse the corpus."""
    root, destination = Path(root), Path(destination)
    if destination.parent.resolve() != root.resolve():
        raise ValueError('Keep the frozen week manifest beside its prepared files')
    if audit_prepared(root)['state']!='pass':raise ValueError('Cannot freeze incomplete preparation')
    from .followups import digest
    names=['spec.json','resolved-spec.json','bundle.json','bundle-provenance.json','rebase-provenance.json','scenes.json','shared-text.json',
           'contract.json','followup-plan.json','summary.json','evaluation-plan.json','realism-review.json']
    paths=[root/name for name in names if (root/name).exists()]
    for folder in ('evaluations','scene-verifications','model-calls'):
        paths.extend((root/folder).glob('*.json') if folder == 'model-calls'
                     else (root/folder).rglob('*.json'))
    files={}
    for path in sorted(paths):
        raw=path.read_bytes()
        value=json.loads(raw)
        if public_record(value) != value:
            raise ValueError('Private replay content cannot enter a frozen week manifest')
        files[str(path.relative_to(root))]=hashlib.sha256(raw).hexdigest()
    body={'version':2,'files':files}
    save(destination,{**body,'corpus_hash':digest(body)})


def import_week(source, root):
    from .followups import digest
    source, root = Path(source), Path(root)
    body=json.loads(source.read_text());claimed=body.pop('corpus_hash')
    if body.get('version') not in (1,2) or digest(body)!=claimed:raise ValueError('Frozen week hash/version mismatch')
    files={}
    for name,value in body['files'].items():
        path=Path(name)
        if path.is_absolute() or '..' in path.parts or path.suffix!='.json':raise ValueError('Unsafe frozen week path')
        if body['version']==2:
            original=source.parent/path
            if not original.resolve().is_relative_to(source.parent.resolve()):raise ValueError('Unsafe frozen week path')
            raw=original.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=value:raise ValueError('Frozen week file hash mismatch: '+name)
            json.loads(raw)
            files[path]=raw
        else:
            files[path]=(json.dumps(value,indent=2,ensure_ascii=False)+'\n').encode()
    # Check every reference before creating a destination or starting paid work.
    root.mkdir(parents=True,exist_ok=False)
    for path,raw in files.items():
        destination=root/path;destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(raw)
    report=audit_prepared(root)
    if report['state']!='pass':raise ValueError('Frozen week failed preparation audit: '+str(report['errors']))
    return root

def prepare(spec: dict, root: Path, *, resume=False, week_start=None) -> dict:
    """Run once and leave a diagnosis even when authoring fails early."""
    if resume and not (root/'resolved-spec.json').exists():raise ValueError('Resume requires an existing authoring directory')
    root.mkdir(parents=True, exist_ok=resume)
    save(root / "spec.json", spec)
    progress = {"state": "running", "stage": "spec validation",
                "started_at": datetime.now(timezone.utc).isoformat(), 'resume':resume,'week_start':week_start}
    save(root / "experiment-status.json", progress)
    try:
        summary = _prepare_body(spec, root, progress)
        audit = audit_prepared(root) if summary['state']=='prepared' else {'state':'fail','errors':summary.get('errors',[])}
        save(root / "run-audit.json", audit)
        if audit["state"] != "pass":
            summary["state"] = "incomplete"
            summary["audit_errors"] = audit["errors"]
            save(root / "summary.json", summary)
    except Exception as exc:
        progress.update({"state": "incomplete", "error": f"{type(exc).__name__}: {exc}",
                         "finished_at": datetime.now(timezone.utc).isoformat()})
        save(root / "experiment-status.json", progress)
        save(root / "summary.json", {"state": "incomplete", "failed_stage": progress["stage"],
                                      "error": progress["error"], "results": []})
        raise
    progress.update(state=summary["state"], stage="finished",
                    finished_at=datetime.now(timezone.utc).isoformat())
    save(root / "experiment-status.json", progress)
    if summary['state']=='prepared':export_week(root,root/'week.json')
    return summary

def main() -> int:
    parser = argparse.ArgumentParser(description="Generate, verify and freeze source messages; never run providers.")
    parser.add_argument("spec", type=Path, help="JSON scene and fact specification")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--week-start",help="Concrete Monday (YYYY-MM-DD) for compiling a weekday week; defaults to this week")
    parser.add_argument('--resume',action='store_true',help='Reuse unchanged verified scenes while fixing preparation; no writer calls for those scenes')
    args = parser.parse_args()
    result = prepare(json.loads(args.spec.read_text()), args.output.resolve(),resume=args.resume,week_start=args.week_start)
    print(args.output)
    return 0 if result['state'] == 'prepared' else 1

if __name__ == "__main__":
    raise SystemExit(main())
