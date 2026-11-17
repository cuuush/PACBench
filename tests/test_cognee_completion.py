import unittest
from provider_adapters.cognee_completion import completed, require_idle
from provider_adapters.cognee_local import QualificationError

def row(id,run,op,done=False,outcome='succeeded'):
    return dict(id=id,pipeline_run_id=run,operation_name=op,ended_at='now' if done else None,outcome=outcome if done else None)

class CompletionTests(unittest.TestCase):
    def test_background_acceptance_and_cognify_are_insufficient(self):
        r=[row('a','a','remember',True),row('b','b','cognify_pipeline',True)]
        self.assertFalse(completed(r,set()))
        r.append(row('c','c','improve'))
        self.assertFalse(completed(r,set()))
        r.append(row('d','c','improve',True))
        self.assertTrue(completed(r,set()))
    def test_historical_started_is_closed_by_terminal_same_run(self):
        r=[row('a','a','cognify_pipeline'),row('b','a','cognify_pipeline',True),row('c','c','improve',True)]
        self.assertTrue(completed(r,set()))
    def test_old_completion_does_not_qualify_new_operation(self):
        self.assertFalse(completed([row('a','a','improve',True)],{'a'}))
    def test_failed_improve_blocks(self):
        with self.assertRaises(QualificationError):completed([row('a','a','improve',True,'errored')],set())

    def test_prior_background_work_blocks_next_import(self):
        with self.assertRaises(QualificationError):require_idle([row('a','a','memify_pipeline')])
        require_idle([row('a','a','memify_pipeline'),row('b','a','memify_pipeline',True)])
