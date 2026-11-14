"""Read-only scored Week cost scenarios; estimates never authorize paid work."""
import argparse
import hashlib
import json
import statistics
from collections import Counter
from decimal import Decimal as D
from pathlib import Path


def stats(values):
    ordered=sorted(values)
    if not ordered:raise ValueError('Missing cost reference sample')
    return dict(n=len(values),mean=str(sum(values,D(0))/len(values)),
                p90=str(ordered[int(D('.9')*(len(ordered)-1))]),max=str(ordered[-1]))


def build(repo,ledger_path, *, all_luna=False):
    raw=ledger_path.read_bytes();ledger=json.loads(raw);calls=ledger['calls']
    reference=repo/'runs/pizza-week-fresh-native-20261113'
    drivers=[];native=[];call_counts=[];excluded_driver=excluded_native=0
    for p in reference.glob('*/repeat-1/*/turns/*/turn.json'):
        turn=json.loads(p.read_text());g=turn.get('hermes_gateway',{});root=p.parents[2]
        if not g.get('real_finished_at') or turn.get('returncode')!=0:
            excluded_driver+=1;excluded_native+=1;continue
        prefix='pacbench-'+hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]+'-'+root.name
        window=[r for r in calls if g['real_started_at']<=r['started']<=g['real_finished_at']]
        for role in ['driver','memory']:
            rows=[r for r in window if r.get('project')==prefix+'-'+role]
            if (role=='driver' and not rows) or any(r['state']!='settled' or r.get('input_tokens') is None or r.get('output_tokens') is None for r in rows):
                if role=='driver':excluded_driver+=1
                else:excluded_native+=1
                continue
            if role=='driver':
                drivers.append(sum(((D(r['input_tokens'])*D('.0000001')+D(r['output_tokens'])*D('.0000005')) if all_luna else D(r['accounted']) for r in rows),D(0)));call_counts.append(D(len(rows)))
            else:
                # Price observed DeepSeek retrieval token counts at Luna base
                # rates. This is a counterfactual, not measured Luna recall.
                native.append(sum((D(r['input_tokens'])*D('.0000001')+D(r['output_tokens'])*D('.0000005') for r in rows),D(0)))
    judges=[]
    for p in reference.glob('*/model-calls/*.json'):
        r=json.loads(p.read_text());u=r.get('usage') or {}
        if r.get('status')=='ok' and 'cost' in u:judges.append(D(u['prompt_tokens'])*D('.0000001')+D(u['completion_tokens'])*D('.0000005') if all_luna else D(str(u['cost'])))
    samples=dict(driver_turn=stats(drivers),driver_calls_per_turn=stats(call_counts),judge_request=stats(judges),
                 hypothetical_luna_native_during_turn=stats(native))
    n=D(333);ingest=D('.165060375')
    lower=ingest+n*sum((D(samples[k]['mean']) for k in ['driver_turn','judge_request','hypothetical_luna_native_during_turn']),D(0))
    upper=3*ingest+2*n*sum((D(samples[k]['p90']) for k in ['driver_turn','judge_request','hypothetical_luna_native_during_turn']),D(0))
    exposure=sum((D(r['accounted']) for r in calls),D(0));available=D(ledger['config']['cap'])-exposure
    from .direct_accounting import request_cost_bound
    lunar=ledger['route_configs']['luna-bedrock-global'];peak_luna=request_cost_bound(lunar,lunar['max_completion_tokens'],shared_context=True)
    deep=ledger['config'];peak_deep=request_cost_bound(deep,deep['max_completion_tokens'])
    from decimal import ROUND_CEILING
    envelope=(upper+3*peak_luna).to_integral_value(rounding=ROUND_CEILING) if all_luna else D(8)
    proposed_cap=(exposure+envelope).to_integral_value(rounding=ROUND_CEILING)
    assert ledger_path.read_bytes()==raw
    return {'version':'scored-week-budget-plan-v1','state':'blocked-no-paid-execution','paid_calls':0,
            'sources':125,'providers':9,'questions_each':37,'answer_targets':333,'criteria_targets':1395,
            'routes':{'native_memory':'openai/gpt-6-luna @ amazon-bedrock/global',
                      'hermes_and_judge':'openai/gpt-6-luna @ amazon-bedrock/global' if all_luna else 'deepseek/deepseek-v4.1-flash-1017 @ deepinfra/fp8/flex',
                      'judge_effort':'low','judge_protocol':'semantic-judge-date-scope-v2','prompt_protocol':'native-question-v2'},
            'samples':samples,'excluded_driver_turns':excluded_driver,'excluded_native_windows':excluded_native,
            'planning_scenarios_usd':{'all_luna':all_luna,'repricing_scope':'For all-Luna, existing DeepSeek Hermes/judge token counts are repriced at Luna base rates; not observed Luna scoring charges. Cache/long-context and model-dependent output variation can change actuals.','mean_one_pass':str(lower),'stress_two_turns_p90_three_imports':str(upper),
                'formula_low':'Measured Luna full import +333*(mean priced Hermes turn +mean priced judgment +hypothetical Luna-priced native retrieval window)',
                'formula_high':'3*measured Luna full import +666*(p90 priced Hermes turn +p90 priced judgment +p90 hypothetical Luna retrieval)',
                'limits':'Planning scenarios, not confidence intervals or guaranteed completion bounds. Native retrieval tokens came from DeepSeek and Luna can differ. Native background calls outside answer windows, unobserved failed calls, long reasoning tails and additional retries may exceed scenarios. Local compute excluded. Historical liabilities are separate and retained.'},
            'cumulative_vs_inflight':{'measured_clean_import_actual_usd':str(ingest),'uncapped_luna_single_reservation_usd':str(peak_luna),
                'uncapped_answer_judge_reservation_usd':str(peak_luna if all_luna else peak_deep),
                'two_native_plus_one_answer_reservations_usd':str(3*peak_luna if all_luna else 2*peak_luna+peak_deep),
                'explanation':'Reservations are transient exposure, not additional actual spend. Sequential providers reduce overlaps; native internal concurrency remains native except the measured explicit serial conditions. All reservations and unknowns count inside both aggregate cap and optional cohort ceiling. Future native concurrency is not guaranteed by a measured peak of1.'},
            'financial_plan':{'unchanged_cap_usd':str(ledger['config']['cap']),'existing_exposure_usd':str(exposure),
                'available_usd':str(available),'proposed_new_cohort_liability_ceiling_usd':str(envelope),
                'additional_headroom_for_envelope_usd':str(max(D(0),envelope-available)),
                'minimum_total_cap_for_envelope_usd':str(exposure+envelope),'suggested_approval_cap_usd':str(proposed_cap),
                'single_luna_admission_deficit_usd':str(max(D(0),peak_luna-available)),
                'decision':f'Proposed aggregate capUSD{proposed_cap}, with a separate new cohort liability ceilingUSD{envelope}; retain all historic liabilities. This is a financial stop envelope, not guaranteed full completion. No cap or private authorization changed.'},
            'execution_plan':['Offline route/accounting tests first; no inference until explicit funding decision and re-preflight.',
                'Use one fresh chronological store per provider; import only eligible daily sources before each frozen question.',
                'Run providers sequentially. Retain measured native serial settings for OpenViking/Hindsight/Memcal and unchanged Honcho clock/thinking setting.',
                f'Use one shared cohort ID with a liability ceilingUSD{envelope}; unchanged aggregate cap remains independently binding.',
                'Stop on cap/cohort denial, unresolved request or native failure under existing rules; never release an unknown hold to admit another call.',
                'Retain valid cases and rerun only failed cases under verified recovery. Report failures, costs and partial coverage honestly.'],
            'ledger_unchanged':True,'sensitive_data_included':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger',type=Path,default=Path('/workspace/shared/pacbench-openrouter-pizza-day-20261112/ledger.json'))
    parser.add_argument('--output',type=Path)
    parser.add_argument('--all-luna',action='store_true',help='Reprice previous scoring token samples for the explicitly selected all-Luna condition')
    args=parser.parse_args();r=build(Path(__file__).resolve().parents[1],args.ledger,all_luna=args.all_luna)
    text=json.dumps(r,indent=2)+'\n'
    if args.output:args.output.write_text(text)
    print(text)

if __name__=='__main__':main()
