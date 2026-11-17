"""Owner-dispatched, idempotent admission protocol; no remote permit or server.

A production authenticated private request/response channel is not available in
this executor. This protocol is prepared offline; it does not issue transferable
funding tickets, activate another ledger, or authorize a network sender.
"""
import hashlib
import json
from copy import deepcopy
from .direct_accounting import now,money

PROTOCOL='single-dispatch-owner-v1'


class SingleAdmissionOwner:
    def __init__(self,budget,*,diagnostic_id,executor_id,project,cohort_id,ceiling_usd):
        if budget.profile_name!='luna-bedrock-global':raise ValueError('Exact Luna Bedrock Global route required')
        if not project.endswith('-memcal-memory'):raise ValueError('Standalone MemCal memory capability only')
        if not diagnostic_id or not executor_id:raise ValueError('Bound diagnostic/executor identity required')
        self.budget=budget;self.diagnostic_id=diagnostic_id;self.executor_id=executor_id;self.project=project
        self.scope={'id':cohort_id,'limit_usd':str(money(ceiling_usd))}

    def dispatch_offline(self,*,attempt_id,body,sender,authenticated_executor_id):
        """Exercise dispatch state machine with an IN-PROCESS synthetic sender.

        Not exposed as a funded RPC. A future live bridge must authenticate its
        caller and keep sender + original ledger in this owner process.
        """
        with self.budget.locked(write=False) as ledger:
            if ledger.get('offline_fixture')!=PROTOCOL:
                raise RuntimeError('Offline protocol cannot use a live task ledger')
        if authenticated_executor_id!=self.executor_id:raise ValueError('Executor identity mismatch')
        if not isinstance(attempt_id,str) or not attempt_id or len(attempt_id)>128:raise ValueError('Attempt identity required')
        identity=[self.diagnostic_id,self.executor_id,attempt_id]
        key=hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        fingerprint=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        binding={'identity':identity,'request_sha256':fingerprint,'project':self.project,
            'profile':self.budget.profile_name,'scope':self.scope}
        with self.budget.locked() as ledger:
            rows=ledger.setdefault('single_owner_attempts',{});old=rows.get(key)
            if old is not None:
                if old['binding']!=binding:raise ValueError('Attempt payload or scope conflict')
                return deepcopy(old)  # Including admitting/ambiguous: never auto resend.
            rows[key]={'protocol':PROTOCOL,'binding':binding,'state':'admitting','created_at':now()}
        try:
            reservation=self.budget.reserve(self.project,body,cohort_scope=self.scope)
        except BaseException as exc:
            with self.budget.locked() as ledger:
                ledger['single_owner_attempts'][key].update(state='admission-refused',error_type=type(exc).__name__)
            raise
        # A crash before this mapping leaves a conservative orphan reservation,
        # plus durable 'admitting'. Neither permits a second dispatch.
        with self.budget.locked() as ledger:
            ledger['single_owner_attempts'][key].update(state='dispatching',reservation_id=reservation['id'])
        try:
            # Only the owner invokes sender. Returning a permit is forbidden.
            status,headers,data=sender(reservation['request'])
            self.budget.settle(reservation['id'],status,data,headers=headers,body_complete=True)
            with self.budget.locked() as ledger:
                call=next(c for c in ledger['calls'] if c['id']==reservation['id'])
                entry=ledger['single_owner_attempts'][key]
                entry.update(state=call['state'],finished_at=now(),generation_id=call.get('upstream_request_id'),
                    actual_cost_usd=call.get('settled_charge_usd'))
                return deepcopy(entry)
        except BaseException as exc:
            # Original reservation and earliest response IDs remain untouched.
            with self.budget.locked() as ledger:
                ledger['single_owner_attempts'][key].update(state='unknown',error_type=type(exc).__name__)
            raise

    def dispatch(self,**kwargs):
        raise RuntimeError('Live shared admission requires a supported authenticated private owner-dispatch channel')
