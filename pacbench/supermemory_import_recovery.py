"""Verify an accepted Supermemory import through native read-only APIs.

Never calls add, rewrites memories, flushes jobs or hides the failed receipt.
"""
import argparse,hashlib,json,os,time
from pathlib import Path
from .followups import save
from .direct_accounting import now


def qualify_source(raw,*,identifier,text,metadata):
    canonical=raw.get('content');actual=raw.get('metadata')
    normalization='none' if canonical==text else 'native-single-terminal-lf-elision-v1' if text.endswith('\n') and canonical==text[:-1] else None
    if (raw.get('id')!=identifier or normalization is None or not isinstance(actual,dict) or
        any(actual.get(k)!=v for k,v in metadata.items()) or
        set(actual)-set(metadata)-{'temporalContext','temporalEventStartMs','temporalEventEndMs'}):
        raise ValueError('Native source or submitted metadata differs beyond witnessed canonicalization')
    return normalization


def qualify(raw,status,*,identifier,text,metadata):
    qualify_source(raw,identifier=identifier,text=text,metadata=metadata)
    if (
        status.get('id')!=identifier or status.get('status')!='done' or status.get('dreaming_status')!='done'):
        raise ValueError('Accepted native source is not verified fully processed and exact')


def recover(output,day,*,refresh_stalled=False):
    from .native_pilot import provision,stop_arm_model_work
    from .openrouter_route import MODEL
    from .provider_loader import provider_module
    from provider_adapters.supermemory_native import namespace,completion_witness
    from supermemory import Supermemory
    from .import_lifecycle import scan
    output=output.resolve();root=output/'repeat-1/supermemory';path=root/'ingestion'/(day+'.json')
    original=path.read_bytes();record=json.loads(original);native=record.get('result',{})
    failure=native.get('native_error',{})
    if (record.get('ready') is not False or failure.get('commit_state')!='accepted' or
        failure.get('phase')!='document-status' or not native.get('result',{}).get('id')):
        raise ValueError('Only an identified accepted status-read failure is recoverable')
    if scan(root,'accepted-supermemory-recovery')[0]:raise ValueError('Previous owned worker is still alive')
    parent=root/'accepted-import-recovery'/day
    index=1
    while (parent/f'attempt-{index}').exists():index+=1
    history=parent/f'attempt-{index}';history.mkdir(parents=True,exist_ok=False)
    (history/'original.json').write_bytes(original)
    stop=history/'STOP';os.environ['PACBENCH_STOP_FILE']=str(stop)
    summary=json.loads((output/'summary.json').read_text());start=time.monotonic();env=None
    try:
        env=provision(root,'supermemory',MODEL,local_timezone=summary['process_timezone'],resume=True,inference_route='openrouter')
        env['PACBENCH_STOP_FILE']=str(stop)
        # The resolver reads this existing native home; no SDK namespace change.
        os.environ['HERMES_HOME']=str(root/'hermes-home')
        cfg,tag=namespace(provider_module('supermemory'),root/'hermes-home')
        if tag!=native['container_tag']:raise ValueError('Native namespace differs')
        client=Supermemory(api_key=env['SUPERMEMORY_API_KEY'],base_url=cfg['base_url'],max_retries=0,timeout=30)
        identifier=native['result']['id'];source=output/'sources'/day
        text=(source/'messages.txt').read_text();meta={'source':'daily-transcript','collected_at':record['at']}
        marker=parent/'native-refresh-attempt.json'
        observations=[];read_failures=[];deadline=time.monotonic()+300;refreshed=marker.exists()
        while True:
            from .stop import check_stop
            from .gateway_client import require_live_cost_boundary
            import urllib.error
            check_stop(stop);require_live_cost_boundary()
            try:witness=completion_witness(cfg['base_url'],env['SUPERMEMORY_API_KEY'],tag,identifier,include_native_source=True)
            except (OSError,TimeoutError) as error:
                if isinstance(error,urllib.error.HTTPError) and error.code not in {429,500,502,503,504}:raise
                read_failures.append({'at':now(),'error_class':type(error).__name__,'read_only':True})
                save(history/'read-failures.private.json',read_failures)
                if time.monotonic()>=deadline:raise RuntimeError('Native read-only readiness window expired; no duplicate document write sent') from error
                time.sleep(1);continue
            raw=witness.pop('native_source');raw['system']={'status':witness['status']}
            normalization=qualify_source(raw,identifier=identifier,text=text,metadata=meta)
            status={'id':identifier,'status':witness['status'],'dreaming_status':witness['dreaming_status'],'native_completion_witness':witness}
            observed={**status,'content':raw.get('content'),'metadata':raw.get('metadata'),'native_system':raw.get('system'),'request_namespace':tag}
            if not observations or observed!=observations[-1]:observations.append(observed)
            save(history/'status-observations.private.json',observations)
            if status.get('status')=='done' and status.get('dreaming_status')=='done':break
            if status.get('status') in {'failed','error'} or time.monotonic()>=deadline:
                if refresh_stalled and not refreshed:
                    with marker.open('x') as stream:json.dump({'state':'sending','id':identifier,'source_sha256':hashlib.sha256(text.encode()).hexdigest(),'at':now()},stream)
                    updated=client.documents.update(tag,identifier,content=text,dreaming='instant')
                    if updated.id!=identifier:raise RuntimeError('Native update changed the stable document ID')
                    save(marker,{'state':'accepted','id':identifier,'source_sha256':hashlib.sha256(text.encode()).hexdigest(),'at':now()})
                    refreshed=True;deadline=time.monotonic()+300;continue
                raise RuntimeError('Native accepted import did not reach complete readiness')
            time.sleep(1)
        qualify(raw,status,identifier=identifier,text=text,metadata=meta)
        if hashlib.sha256(text.encode()).hexdigest()!=record['document_sha256']:raise ValueError('Frozen source changed')
        proof={'protocol':'supermemory-accepted-read-only-recovery-v1','original_sha256':hashlib.sha256(original).hexdigest(),
            'verified_at':now(),'sources_resubmitted':int(refreshed),'new_documents_added':0,'same_id_reprocessing_requests':int(refreshed),
            'native_writes':int(refreshed),'observation_wall_s':round(time.monotonic()-start,6),
            'native_update_commit_state':json.loads(marker.read_text())['state'] if marker.exists() else 'not-requested',
            'source_normalization':normalization,'delivered_sha256':hashlib.sha256(text.encode()).hexdigest(),
            'native_canonical_sha256':hashlib.sha256(raw['content'].encode()).hexdigest(),
            'transient_status_read_failures':len(read_failures),
            'original_worker_returncode':record.get('returncode'),'original_finished':record.get('usage',{}).get('finished'),
            'scope':'Native readiness observation; optional single SDK update reprocesses the identical source under its existing stable ID. Original failed worker and source remain archived.'}
        native.update(ready=True,reason='',processing_status=[*native.get('processing_status',[]),*observations],recovery=proof)
        native.pop('native_error',None)
        record.update(state='ready',ready=True,returncode=0,recovery=proof)
        record['usage']['finished']=proof['verified_at']
        save(history/'observation.json',{'proof':proof,'status':status})
        save(path,record)
        return proof
    finally:
        stop_arm_model_work(root)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);p.add_argument('day');p.add_argument('--refresh-stalled',action='store_true');a=p.parse_args()
    from .openrouter_direct import direct_environment
    with direct_environment(os.environ['OPENROUTER_API_KEY']):proof=recover(a.output,a.day,refresh_stalled=a.refresh_stalled)
    print(json.dumps({'state':'verified-native-readiness','sources_resubmitted':proof['sources_resubmitted'],'verified_at':proof['verified_at']}))


if __name__=='__main__':main()
