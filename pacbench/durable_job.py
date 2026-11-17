"""Detach an explicitly requested job without duplicate launches or secret argv."""
from pathlib import Path
import json,os,subprocess


def launch(command,output):
    output=Path(output).resolve()
    if output.exists():raise FileExistsError('Job output already exists')
    output.parent.mkdir(parents=True,exist_ok=True)
    receipt=output.with_name(output.name+'.launch.private.json')
    log=output.with_name(output.name+'.controller.private.log')
    # Exclusive receipt creation also rejects two concurrent launch attempts.
    fd=os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as evidence:
        evidence.write(json.dumps({'state':'launching','output':str(output)}));evidence.flush()
        try:
            logfd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(logfd,'wb') as stream:
                child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,
                    start_new_session=True,close_fds=True)
            result={'state':'launched','pid':child.pid,'output':str(output),'private_log':str(log),
                'scope':'Child preflight and execution may still fail; this receipt only confirms process launch.'}
        except Exception:
            evidence.seek(0);evidence.truncate();json.dump({'state':'launch-failed','output':str(output)},evidence)
            raise
        evidence.seek(0);evidence.truncate();json.dump(result,evidence)
    return result
