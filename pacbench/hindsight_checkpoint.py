"""Verify completion of native Hindsight operations after an observation."""
import asyncio
import time

from .hermes_gateway import GatewayLifecycleError


def settled(endpoint,bank,*,deadline_s=70):
    from hindsight_client import Hindsight
    async def read():
        client=Hindsight(endpoint,timeout=15,max_attempts=1)
        try:
            deadline=time.monotonic()+deadline_s
            while True:
                result=await client.operations.list_operations(bank_id=bank,limit=100,offset=0)
                ops=[item.model_dump(mode='json') for item in result.operations]
                if len(ops)!=result.total or any(item['status']=='failed' for item in ops):
                    raise GatewayLifecycleError('Hindsight native operation accounting failed')
                if all(item['status']=='completed' for item in ops):
                    return ops
                if time.monotonic()>=deadline:
                    raise GatewayLifecycleError('Hindsight observation operations did not drain')
                await asyncio.sleep(.5)
        finally:await client.aclose()
    return asyncio.run(read())
