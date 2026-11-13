"""Observed import latency; unknown portions stay explicitly unclassified."""
from __future__ import annotations
from datetime import datetime,timezone
from pathlib import Path
import json,hashlib,argparse


def stamp(value):return datetime.fromisoformat(value).timestamp()


def union_seconds(intervals):
    total=0.;end=None
    for left,right in sorted(intervals):
        if right<left:raise ValueError('Reversed timing interval')
        total+=max(0,right-max(left,end if end is not None else left));end=max(right,end if end is not None else right)
    return total


def build(components,calls):
    result=[]
    for component in map(Path,components):
        summary=json.loads((component/'summary.json').read_text())
        if summary.get('mode')!='native-import-validation':continue
        cohort=json.loads((component.parent/'cohort.json').read_text());launch=next((r['started'] for r in cohort.get('results',[]) if r['provider']==component.name),cohort['started'])
        arm=component/'repeat-1'/component.name
        project='pacbench-'+hashlib.sha256(str(arm.resolve()).encode()).hexdigest()[:12]+'-'+component.name+'-memory'
        own=[c for c in calls if c['project']==project];batches=[]
        for path in sorted((arm/'ingestion').glob('*.json')):
            r=json.loads(path.read_text());usage=r.get('usage',{});start=usage.get('started');finish=usage.get('finished');intervals=[];unknown=[]
            if start and finish:
                left,right=stamp(start),stamp(finish)
                for c in own:
                    a=stamp(c['started']);b=stamp(c['finished']) if c.get('finished') else None
                    if a<right and (b is None or b>left):
                        if b is None:unknown.append(c['id'])
                        else:intervals.append((max(a,left),min(b,right)))
                span=union_seconds(intervals);wall=right-left
            else:span=wall=None
            batches.append({'day':path.stem,'recorded_ready':r.get('ready'),'adapter_wall_seconds':r.get('latency_s'),
                'observation_window_start':start,'observation_window_end':finish,'observation_window_seconds':wall,
                'finished_api_span_union_seconds':span,'unfinished_or_abandoned_api_requests_overlapping_window':unknown,
                'non_api_or_unresolved_window_seconds':max(0,wall-span) if wall is not None else None,
                'recovery_overlay_present':(arm/'ingestion-recovery'/path.name).exists()})
        completion=component/'recovered-processing-completion.json'
        if not completion.exists():completion=component/'import-verification.json'
        # File mtime is an explicit upper-bound observation, not a native
        # completion timestamp. It includes controller pauses and operator work.
        upper=datetime.fromtimestamp(completion.stat().st_mtime,timezone.utc).isoformat() if completion.exists() else None
        evidence=json.loads(completion.read_text()) if completion.exists() else {}
        complete=(summary['state']=='processing-complete' or evidence.get('completed_batches')==7)
        last_ready=next((b['observation_window_end'] for b in batches if b['day']=='2026-10-11' and b['recorded_ready']),None)
        result.append({'provider':component.name,'attempt':component.parent.name+'/'+component.name,
            'launch_at':launch,'native_last_batch_ready_at':last_ready if complete else None,
            'end_to_end_to_native_readiness_seconds':stamp(last_ready)-stamp(launch) if complete and last_ready else None,
            'launch_scope':'provider launch when persisted; otherwise shared concurrent cohort launch',
            'completion_evidence_file_written_at':upper,
            'elapsed_to_verification_observation_seconds':stamp(upper)-stamp(launch) if upper else None,
            'adapter_seconds_sum_available_batches':sum(b['adapter_wall_seconds'] or 0 for b in batches),
            'available_receipt_count':len(batches),'missing_or_recovered_batches':7-sum(b['recorded_ready'] is True for b in batches),
            'batches':batches})
    return {'version':'native-import-timings-v1','attempts':result,
        'interpretation':['Elapsed to verification includes provisioning, operator pauses and recovery. It is not an intrinsic provider latency ranking.',
            'Adapter wall includes native ingestion, model/API requests, embedding, native queues and readiness polling.',
            'API span is the union of observed admission-to-response intervals clipped to each receipt window; overlapping calls are not double-counted.',
            'Residual time cannot be separated into queueing, local provider work and harness overhead from these receipts alone. Unknown API intervals remain explicit.',
            'Original Cognee early receipts observed cognify only; full-operation timing additionally requires native improve completion records.',
            'Supermemory retained its completed interleaved benchmark; it has no comparable fresh import-only wall time.']}


def main():
    p=argparse.ArgumentParser();p.add_argument('--ledger',type=Path,required=True);p.add_argument('--components',type=Path,nargs='+',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    r=build(a.components,json.loads(a.ledger.read_text())['calls']);a.output.write_text(json.dumps(r,indent=2)+'\n')

if __name__=='__main__':main()
