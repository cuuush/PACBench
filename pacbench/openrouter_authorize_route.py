"""Append an explicitly approved route to the existing cap; never create/reset history."""
import argparse,fcntl,json,os
from pathlib import Path
from .openrouter_route import MODEL,ENDPOINT,PROFILE_NAME
from .openrouter_task import STATE
from .openrouter_run import fetch_catalog
from .direct_accounting import money,now


def register(params,authorization,*,state_dir=STATE):
    if PROFILE_NAME!='luna-bedrock-global' or not authorization.strip():
        raise ValueError('Explicit Luna comparison authorization is required')
    state_dir=Path(state_dir);auth_path=state_dir/'authorization.json'
    with (state_dir/'ledger.json').open('r+') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX);state=json.load(stream);auth=json.loads(auth_path.read_text())
        if (state['version']!=1 or money(state['config']['cap'])!=20 or
                auth.get('task')!='pacbench-pizza-day-20261112' or auth.get('approved_total_usd')!='20'):
            raise ValueError('Existing authorized shared task required')
        config={**params,'model':MODEL,'endpoint':ENDPOINT,'cap':'20',
                'request_scope':'import-and-connectivity-probes'}
        existing=state.get('route_configs',{}).get(PROFILE_NAME)
        if existing is not None and existing!=config:raise ValueError('Preserve existing route price/limit configuration')
        grant={'model':MODEL,'endpoint':ENDPOINT,'scope':'Import-only comparison and minimal route probes; benchmark questions paused'}
        routes=auth.setdefault('additional_routes',[])
        if grant not in routes:routes.append(grant)
        tmp=auth_path.with_suffix('.json.tmp')
        with tmp.open('w') as f:
            os.chmod(tmp,0o600);json.dump(auth,f,separators=(',',':'));f.flush();os.fsync(f.fileno())
        tmp.replace(auth_path)
        state.setdefault('route_configs',{})[PROFILE_NAME]=config
        if existing is None:
            state.setdefault('route_authorizations',[]).append({'at':now(),'profile':PROFILE_NAME,
                'authorization':authorization,'cap_changed':False,'historical_rows_changed':False})
        stream.seek(0);json.dump(state,stream,separators=(',',':'));stream.truncate();stream.flush();os.fsync(stream.fileno())
    return {'state':'route-registered','profile':PROFILE_NAME,'model':MODEL,'endpoint':ENDPOINT,
            'cap_usd':'20','inference_calls':0,'historical_holds_released':False}


def main():
    p=argparse.ArgumentParser();p.add_argument('--authorization',required=True);a=p.parse_args()
    params,_=fetch_catalog();print(json.dumps(register(params,a.authorization)))

if __name__=='__main__':main()
