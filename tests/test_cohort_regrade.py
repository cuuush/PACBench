import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pacbench.cohort_regrade import snapshot, Overlay, main
from pacbench.semantic_grade import Claim
from pacbench.workflow_spec import Turn
from pacbench.workflow_progress import WorkflowProgress
from pacbench.workflow_stream import fingerprint


class CohortRegradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runs = [self.make_run('alpha'), self.make_run('beta')]
        self.manifest = self.root/'cohort.json'
        self.manifest.write_text(json.dumps({'schema': 'pacbench-regrade-cohort-v1', 'runs': [
            {'provider': p.parent.name, 'manifest': str(p.relative_to(self.root)),
             'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in self.runs]}))

    def make_run(self, provider, missing=False, future=False):
        d = self.root/provider; d.mkdir()
        identity = {'protocol':'native-document-incremental-v1','condition':{'provider':provider},'evidence_kind':'native'}
        key = fingerprint(identity)
        meta = {'run_key':key,'identity':identity,'evidence_kind':'native'}
        (d/'run.json').write_text(json.dumps(meta))
        with WorkflowProgress(d,key,create=True) as p:
            p.bind('identity',identity);p.bind('plan_registered',True)
            for i in range(2):
                t=Turn('turn'+str(i),'2026-10-12T10:00:00-04:00','What time is the demo?','session','recall',(Claim('time','Demo starts at noon.',('synthetic-evidence',)),))
                p.plan_turn(i,t)
                if missing and i==1:continue
                history=[{'role':'user','content':'Earlier question'},{'role':'assistant','content':'Earlier answer'},{'role':'user','content':t.prompt}]
                surface={'answer':'The demo starts at noon.','history':history,'conversation_times':['2026-10-13T09:00:00-04:00' if future else '2026-10-12T09:00:00-04:00'],
                         'session_id':'session','gateway_identity':'synthetic-only','model_requests':[], 'evidence':{'text':'harmless synthetic evidence'}}
                p.verified_turn(t,surface,{'synthetic':True});p.mark_turn_done(t.id)
                p.claim_grade();p.finish_grade(t.id,[{'id':'time','verdict':'missed','reason':'mock original'}])
        return d/'run.json'

    def plan(self):return snapshot(self.manifest,'luna-context-v4-v1')
    def response(self,p,item=None):
        i=item or p['items'][0]
        return {'plan_id':p['plan_id'],'cell':i['id'],'prompt_sha256':i['prompt_sha256'],'attempt_id':'mock-'+i['id'],
                'response':json.dumps({'verdicts':[{'id':'time','verdict':'correct','reason':'mock response'}]})}

    def test_all_providers_discovered_read_only(self):
        before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        plan=self.plan();self.assertEqual(len(plan['items']),4)
        self.assertEqual(plan['coverage']['alpha']['completed_criteria'],2)
        self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in before})
        self.assertFalse(plan['dispatch_enabled'])

    def test_direct_manifest_and_missing_coverage(self):
        path=self.make_run('partial',missing=True)
        p=snapshot(path,'luna-context-v4-v1');self.assertEqual(p['coverage']['partial']['missing_turns'],['turn1'])

    def test_stale_manifest_and_duplicate_provider(self):
        self.runs[0].write_text(self.runs[0].read_text()+' ')
        with self.assertRaisesRegex(ValueError,'stale'):self.plan()
        m=json.loads(self.manifest.read_text());m['runs']=[m['runs'][1]]*2;self.manifest.write_text(json.dumps(m))
        with self.assertRaisesRegex(ValueError,'duplicate'):self.plan()

    def test_future_context_rejected(self):
        path=self.make_run('future',future=True)
        with self.assertRaisesRegex(ValueError,'future'):snapshot(path,'luna-context-v4-v1')

    def test_unregistered_revision(self):
        with self.assertRaisesRegex(ValueError,'unregistered'):snapshot(self.manifest,'latest')

    def test_resume_never_reclaims_attempt(self):
        p=self.plan();o=Overlay(self.root/'regrade-v1',p);i=p['items'][0]
        o.reserve(i['id'],'attempt-one');o.close()
        o=Overlay(self.root/'regrade-v1',p);self.addCleanup(o.close)
        self.assertEqual(o.report()['states'][i['id']],'ambiguous-attempt')
        with self.assertRaisesRegex(ValueError,'never resend'):o.reserve(i['id'],'attempt-two')
        r=self.response(p);r['attempt_id']='attempt-one';o.import_response(r)
        self.assertEqual(o.report()['providers']['alpha']['credit_delta'],1)
        o.import_response(r)
        r['response']='other'
        with self.assertRaises(ValueError):o.import_response(r)

    def test_invalid_and_unknown_are_not_zero(self):
        p=self.plan();o=Overlay(self.root/'results',p);self.addCleanup(o.close)
        r=self.response(p);r['response']='invalid';o.import_response(r)
        report=o.report();self.assertEqual(report['states'][r['cell']],'invalid-output')
        self.assertTrue(all(d['credit_delta'] is None for d in report['criterion_deltas']))
        self.assertFalse(report['receipt_verified'])

    def test_stale_overlay_and_response(self):
        p=self.plan();o=Overlay(self.root/'results',p);self.addCleanup(o.close)
        r=self.response(p);r['prompt_sha256']='wrong'
        with self.assertRaises(ValueError):o.import_response(r)
        changed=json.loads(json.dumps(p));changed['items'][0]['saved']['surface']['answer']='changed'
        with self.assertRaisesRegex(ValueError,'stale'):Overlay(self.root/'results',changed)

    def test_original_directory_forbidden(self):
        with self.assertRaises(ValueError):Overlay(self.runs[0].parent/'regrade',self.plan())

    def test_revision_only_changes_prompt_supplement(self):
        baseline=self.plan();explicit=snapshot(self.manifest,'luna-explicit-criteria-v1')
        self.assertNotEqual(baseline['plan_id'],explicit['plan_id'])
        for a,b in zip(baseline['items'],explicit['items']):
            self.assertEqual(a['saved'],b['saved']);self.assertEqual(a['options'],b['options'])
            self.assertTrue(b['prompt'].startswith(a['prompt']+'\nExperimental criterion rules v1:'))

    def test_duplicate_criterion_detected(self):
        import sqlite3
        db=sqlite3.connect(self.runs[0].parent/'workflow.sqlite3')
        raw=db.execute('SELECT plan FROM turns WHERE ordinal=0').fetchone()[0]
        turn=json.loads(raw);turn['claims']*=2
        db.execute('UPDATE turns SET plan=?,criteria_count=2 WHERE ordinal=0',(json.dumps(turn),));db.commit();db.close()
        with self.assertRaisesRegex(ValueError,'duplicate'):self.plan()

    def test_artifact_corruption_detected(self):
        with WorkflowProgress(self.runs[0].parent,json.loads(self.runs[0].read_text())['run_key'],read_only=True) as p:
            key=p.db.execute('SELECT surface FROM turns LIMIT 1').fetchone()[0]
            artifact=p._blob_path(key)
        artifact.unlink();artifact.write_text('{}')
        with self.assertRaises((ValueError,RuntimeError)):self.plan()

    def test_cli_saved_response_import_and_resume(self):
        p=self.plan();records=self.root/'responses.json';records.write_text(json.dumps([self.response(p)]))
        args=[str(self.manifest),'--revision','luna-context-v4-v1','--output',str(self.root/'cli-overlay'),'--responses',str(records)]
        with patch('socket.socket',side_effect=AssertionError('network forbidden')),patch('builtins.print') as out:
            main(args);first=json.loads(out.call_args.args[0]);main(args)
            self.assertEqual(first,json.loads(out.call_args.args[0]))
            self.assertEqual(first['providers']['alpha']['credit_delta'],1)

    def test_cli_defaults_to_no_transport(self):
        with patch('socket.socket',side_effect=AssertionError('network forbidden')),patch('builtins.print') as out:
            main([str(self.manifest),'--revision','luna-context-v4-v1'])
            self.assertFalse(json.loads(out.call_args.args[0])['dispatch_enabled'])

if __name__=='__main__':unittest.main()
