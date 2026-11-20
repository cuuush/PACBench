"""Verify completion of native Supermemory writes after an observation."""
import json
import time
import urllib.request

from .hermes_gateway import GatewayLifecycleError


def settled(endpoint,key,namespace):
    deadline=time.monotonic()+100
    while True:
        request=urllib.request.Request(endpoint+'/v3/documents/list',
            data=json.dumps({'containerTags':[namespace],'includeContent':False,'page':1,'limit':50}).encode(),
            headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=10) as response:
            result=json.load(response)
        inventory=[{'id':row.get('customId',row.get('custom_id')) or row['id'],
                    'status':(row.get('system') or {}).get('status') or row.get('status')}
                   for row in result['memories']]
        if not inventory or len(inventory)>=50 or any(row['status'] in ('failed','error',None) for row in inventory):
            raise GatewayLifecycleError('Supermemory native document readiness unavailable')
        if all(row['status']=='done' for row in inventory):return inventory
        if time.monotonic()>=deadline:raise GatewayLifecycleError('Supermemory native writes remained queued')
        time.sleep(.5)
