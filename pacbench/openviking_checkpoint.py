"""Verify the native OpenViking store after observation startup."""
import json
import re
import urllib.request

from .hermes_gateway import GatewayLifecycleError


def verify_open_clone(observation, manifest):
    """Validate native startup housekeeping without ignoring resource changes."""
    endpoint=observation['endpoint']
    if not re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}',endpoint):
        raise GatewayLifecycleError('OpenViking startup proof requires a run-owned loopback endpoint')
    closed=observation['snapshot_manifest']['files']
    opened=manifest['files']
    runtime_files={'_system/queue/queue.db-shm','_system/queue/queue.db-wal',
                   '_system/usage_audit/usage_audit.sqlite3','_system/usage_audit/usage_audit.sqlite3-shm',
                   '_system/usage_audit/usage_audit.sqlite3-wal'}
    changed={path for path in closed.keys() | opened.keys() if closed.get(path)!=opened.get(path)}
    unexpected=sorted(path for path in changed if path not in runtime_files and not re.fullmatch(
            r'vectordb/context/store/(?:CURRENT|LOCK|LOG(?:\.old)?|MANIFEST-[0-9]+|[0-9]+\.(?:log|ldb))',
            path))
    if unexpected:
        raise GatewayLifecycleError('OpenViking startup changed non-runtime store files: '+', '.join(unexpected))
    reports={}
    for uri in observation['native_resource_uris']:
        request=urllib.request.Request(endpoint+'/api/v1/system/consistency',
            data=json.dumps({'uri':uri}).encode(),headers={'Content-Type':'application/json',
                'X-OpenViking-Account':'default','X-OpenViking-User':'default'})
        with urllib.request.urlopen(request,timeout=10) as response:
            report=json.load(response)
        data=report.get('result',{})
        if report.get('status')!='ok' or data.get('ok') is not True or not data.get('expected_count'):
            raise GatewayLifecycleError('OpenViking opened index failed native resource consistency')
        reports[uri]=data
    return {'changed_storage_files':sorted(changed),'index_reports':reports}
