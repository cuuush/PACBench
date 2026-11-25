import collections
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pacbench import judge_development_challenge as challenge
from pacbench.semantic_grade import CONTEXT_JUDGE_PROTOCOL, SCENARIO_ZONE, Claim, judge_prompt


SEED = '9f' * 32
SHORTCUT = [sys.executable, '-m', 'pacbench.judge_development_challenge']


def reply(verdict):
    return json.dumps({'verdicts': [dict(id='criterion', verdict=verdict, reason='synthetic test reply')]})


class JudgeDevelopmentChallengeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = challenge._frozen(challenge.CELLS, challenge.CELLS_SHA256)
        cls.gold = challenge._frozen(challenge.REFERENCE, challenge.REFERENCE_SHA256)
        cls.cells = challenge._blinded_cells(SEED)
        cls.ids = {cell.key: cell.id for cell in cls.cells}

    def observations(self, rubric, **edits):
        packets = {p['id']: p for p in challenge.plan(SEED, rubric)}
        rows = []
        for cell in reversed(self.cells):
            expected = self.gold['families'][cell.family['id']]['cases'][cell.key]['verdict']
            rows.append(dict(id=cell.id, prompt_sha256=packets[cell.id]['prompt_sha256'],
                             response=edits.get(cell.key, reply(expected))))
        return rows

    def compare(self, baseline, candidate):
        return challenge.compare(SEED, 'v4', 'explicit', baseline, candidate)

    def test_frozen_anchors_and_balanced_three_state_gold(self):
        anchors = {
            'roster-completeness': ('lantern supper', 'bake sale', 'Inez and Milo'),
            'communicated-decline': ('dumpling lunch', 'spice swap', 'Sol told Jun'),
            'dietary-uncertainty': ('courtyard picnic', 'lake outing', 'unconfirmed whether Ren can eat sesame'),
            'next-recurrence': ('map club', 'Saturday map sale', 'Monday, October 19, 2026'),
            'venue-cancellation': ('Cedar House', 'the lunch', 'cancelled for the whole group'),
            'entity-venue': ('skillet tasting', 'separate tastings', 'is at Willow Loft'),
        }
        self.assertTrue(self.fixture['synthetic'])
        self.assertEqual(self.fixture['revision'], self.gold['revision'])
        families = self.fixture['families']
        self.assertEqual(len(families), 6)
        self.assertEqual(set(anchors), set(self.gold['families']))
        self.assertEqual(set(anchors), {family['id'] for family in families})
        self.assertEqual(len(self.cells), len(self.ids))
        self.assertEqual(len(self.cells), 24)
        global_lengths = collections.defaultdict(set)
        for family in families:
            with self.subTest(family=family['id']):
                question, prior, claim = anchors[family['id']]
                self.assertIn(question, family['question'])
                self.assertIn(prior, family['conversation'][0]['content'])
                self.assertIn(claim, family['claim'])
                clock = datetime.fromisoformat(family['question_at'])
                self.assertEqual(clock.utcoffset(), clock.astimezone(SCENARIO_ZONE).utcoffset())
                history = [datetime.fromisoformat(row['at']) for row in family['conversation']]
                self.assertEqual(history, sorted(set(history)))
                self.assertTrue(all(turn < clock for turn in history))
                if family['id'] == 'next-recurrence':
                    self.assertEqual(clock.date(), date(2026, 10, 16))
                    monday = clock.date() + timedelta(days=(-clock.weekday()) % 7)
                    self.assertEqual(monday, date(2026, 10, 19))

                key = self.gold['families'][family['id']]
                self.assertTrue(key['allowed_entailments'])
                self.assertEqual(family['cases'].keys(), key['cases'].keys())
                distribution = collections.Counter(row['verdict'] for row in key['cases'].values())
                self.assertEqual(distribution, {'correct': 2, 'missed': 1, 'misrepresented': 1})
                self.assertEqual(len(set(family['cases'].values())), 4)
                lengths = set()
                for case in (cell for cell in self.cells if cell.family['id'] == family['id']):
                    args, options = case.judge_input
                    self.assertEqual(args[0], family['question'])
                    self.assertEqual(args[2], (Claim('criterion', family['claim']),))
                    self.assertEqual(options['conversation'], family['conversation'])
                    self.assertEqual(options['question_at'], family['question_at'])
                    answer = args[1]
                    self.assertEqual(answer.split('. ')[1:], [family['distractor']])
                    label = key['cases'][case.key]
                    self.assertTrue(label['reason'])
                    lengths.add(len(answer.split()))
                    global_lengths[label['verdict']].add(len(answer.split()))
                self.assertLessEqual(max(lengths) - min(lengths), 1)
        self.assertEqual(global_lengths['correct'], global_lengths['missed'])
        self.assertEqual(global_lengths['correct'], global_lengths['misrepresented'])

    def test_blinded_plans_leave_reference_unread_and_change_only_rubric(self):
        with patch.object(challenge, 'REFERENCE', challenge.REFERENCE.with_suffix('.absent')):
            baseline = challenge.plan(SEED, 'v4')
            candidate = challenge.plan(SEED, 'explicit')
            reshuffled = challenge.plan('a1' * 32, 'v4')
        self.assertEqual(baseline, challenge.plan(SEED, 'v4'))
        self.assertEqual(len({p['id'] for p in baseline}), 24)
        self.assertFalse({p['id'] for p in baseline} & {p['id'] for p in reshuffled})
        self.assertCountEqual([p['prompt'] for p in baseline], [p['prompt'] for p in reshuffled])
        self.assertNotEqual([p['prompt'] for p in baseline], [p['prompt'] for p in reshuffled])
        self.assertNotEqual([cell.family['id'] for cell in self.cells],
                            [family['id'] for family in self.fixture['families'] for _ in range(4)])
        for cell, old, new in zip(self.cells, baseline, candidate, strict=True):
            with self.subTest(cell=cell.id):
                self.assertEqual(cell.id, old['id'])
                self.assertEqual(old['id'], new['id'])
                self.assertRegex(cell.id, r'^[0-9a-f]{32}$')
                family = cell.family
                old_prompt = judge_prompt(family['question'],
                                          family['cases'][cell.key] + ' ' + family['distractor'],
                                          (Claim('criterion', family['claim']),),
                                          question_at=family['question_at'], conversation=family['conversation'],
                                          judge_protocol=CONTEXT_JUDGE_PROTOCOL)
                self.assertEqual(old['prompt'], old_prompt)
                self.assertEqual(new['prompt'], old_prompt + '\nExperimental criterion rule: ' +
                                 family['explicit_rule'])
                key = self.gold['families'][family['id']]
                for packet in (old, new):
                    self.assertEqual(packet.keys(), {'id', 'prompt', 'prompt_sha256'})
                    self.assertEqual(packet['prompt_sha256'], hashlib.sha256(packet['prompt'].encode()).hexdigest())
                    for private in (family['id'], key['allowed_entailments'], key['cases'][cell.key]['reason'], SEED):
                        self.assertNotIn(private, packet['prompt'])

    def test_modified_fixtures_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / 'modified.json'
            changed.write_bytes(challenge.CELLS.read_bytes() + b' ')
            with patch.object(challenge, 'CELLS', changed):
                with self.assertRaisesRegex(ValueError, 'frozen fixture digest'):
                    challenge.plan(SEED, 'v4')
            changed.write_bytes(challenge.REFERENCE.read_bytes() + b' ')
            with patch.object(challenge, 'REFERENCE', changed):
                with self.assertRaisesRegex(ValueError, 'frozen fixture digest'):
                    self.compare([], [])

    def test_two_sided_tie_and_format_only_recovery_have_no_winner(self):
        candidate = self.observations('explicit')
        tied = self.compare(self.observations('v4'), candidate)
        self.assertEqual(tied['comparison'], 'no-winner')
        self.assertEqual(tied['both_sides_correct'], {'baseline': 6, 'candidate': 6})
        self.assertEqual(tied['error_examples'], [])
        bad_format = '```json\n' + reply('correct') + '\n```'
        repaired = self.compare(self.observations('v4', lilac=bad_format), candidate)
        self.assertEqual(repaired['comparison'], 'no-winner')
        self.assertEqual(repaired['invalid_to_valid'], [self.ids['lilac']])
        self.assertEqual(repaired['arms']['baseline']['states']['invalid'], 1)
        self.assertEqual(repaired['families']['roster-completeness']['paired_gains'], 0)
        self.assertEqual(repaired['both_sides_correct'], {'baseline': 5, 'candidate': 6})

    def test_semantic_family_gain_and_actionable_error_examples(self):
        result = self.compare(self.observations('v4', lilac=reply('missed'), flint=reply('missed')),
                              self.observations('explicit'))
        family = result['families']['roster-completeness']
        self.assertEqual((family['paired_valid'], family['paired_gains'], family['paired_losses']), (4, 2, 0))
        self.assertEqual(family['candidate']['positives_correct'], 2)
        self.assertEqual(family['candidate']['omissions_correct'], 1)
        self.assertEqual(family['candidate']['conflicts_correct'], 1)
        self.assertEqual(result['previously_missed_correct_rescued'], [self.ids['lilac']])
        self.assertEqual(result['both_sides_correct'], {'baseline': 5, 'candidate': 6})
        self.assertEqual(result['paired_improved_families'], ['roster-completeness'])
        self.assertEqual(result['comparison'], 'candidate-development-improvement')
        examples = {row['id']: row for row in result['error_examples']}
        example = examples[self.ids['flint']]
        self.assertIn('Noor', example['answer'])
        self.assertIn('adds Noor', example['reference_reason'])
        self.assertEqual((example['baseline']['verdict'], example['candidate']['verdict'], example['gold']),
                         ('missed', 'misrepresented', 'misrepresented'))

    def test_new_false_accepts_invalids_and_regression_each_block_gain(self):
        baseline = self.observations('v4', lilac=reply('missed'), moss=reply('missed'))
        false_accept = self.compare(baseline, self.observations('explicit', clay=reply('correct')))
        self.assertEqual(false_accept['paired_improved_families'], ['roster-completeness'])
        self.assertEqual(false_accept['new_false_accepts'], [self.ids['clay']])
        self.assertEqual(false_accept['comparison'], 'no-winner')
        duplicates = reply('correct').replace('"verdict": "correct"', '"verdict": "missed", "verdict": "correct"')
        invalid = self.compare(baseline, self.observations('explicit', pearl=duplicates))
        self.assertEqual(invalid['new_invalids'], [self.ids['pearl']])
        self.assertEqual(invalid['paired_improved_families'], ['roster-completeness'])
        self.assertEqual(invalid['comparison'], 'no-winner')
        regression = self.compare(baseline, self.observations('explicit', jade=reply('missed')))
        self.assertEqual(regression['paired_regressed_families'], ['dietary-uncertainty'])
        self.assertEqual(regression['comparison'], 'no-winner')

    def test_missing_and_abstention_stay_distinct_from_valid_omission(self):
        baseline = [row for row in self.observations('v4', brass=reply('abstain')) if row['id'] != self.ids['opal']]
        observations = self.observations('explicit', brass=reply('correct'))
        result = self.compare(baseline, observations)
        self.assertEqual(result['arms']['baseline']['states'], dict(valid=22, missing=1, abstained=1, invalid=0))
        self.assertEqual(result['families']['next-recurrence']['baseline']['omissions_correct'], 0)
        self.assertEqual(result['families']['next-recurrence']['candidate']['omissions_correct'], 1)
        self.assertEqual(result['new_false_accepts'], [])
        self.assertEqual(result['false_accepts_without_usable_baseline'], [self.ids['brass']])
        self.assertEqual(result['comparison'], 'no-winner')

    def test_different_rubric_unknown_duplicate_ids_and_unbound_replies_are_refused(self):
        baseline = self.observations('v4')
        candidate = self.observations('explicit')
        row = baseline[0]
        with self.assertRaisesRegex(ValueError, 'prompt digest differs'):
            self.compare(candidate, candidate)
        with self.assertRaisesRegex(ValueError, 'unknown or duplicate'):
            self.compare([row, row], candidate)
        with self.assertRaisesRegex(ValueError, 'unknown or duplicate'):
            self.compare([{**row, 'id': 'unrelated-cell'}], candidate)
        with self.assertRaisesRegex(ValueError, 'prompt digest differs'):
            self.compare([{**row, 'prompt_sha256': '0' * 64}], candidate)

    def test_cli_plan_and_compare_roundtrip_with_saved_synthetic_replies(self):
        command = subprocess.run(SHORTCUT + ['plan', '--seed', SEED, '--rubric', 'v4'],
                                 capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(command.stdout), challenge.plan(SEED, 'v4'))
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / 'baseline.json'
            candidate = Path(directory) / 'candidate.json'
            baseline.write_text(json.dumps(self.observations('v4', ash=reply('correct'))), encoding='utf-8')
            candidate.write_text(json.dumps(self.observations('explicit')), encoding='utf-8')
            command = subprocess.run(SHORTCUT + ['compare', '--seed', SEED, '--baseline-rubric', 'v4',
                                                  '--candidate-rubric', 'explicit', str(baseline), str(candidate)],
                                     capture_output=True, text=True, check=True)
        output = json.loads(command.stdout)
        self.assertEqual(output['scope'], 'synthetic development evidence')
        self.assertEqual(output['paired_improved_families'], ['entity-venue'])
        self.assertEqual(output['comparison'], 'candidate-development-improvement')
        self.assertNotEqual(subprocess.run(SHORTCUT + ['--live'], capture_output=True).returncode, 0)
        self.assertNotEqual(subprocess.run(SHORTCUT + ['plan', '--seed', 'no', '--rubric', 'v4'],
                                           capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
