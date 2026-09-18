"""The private cost hint describes comparable, fully evaluated observations"""
from copy import deepcopy
import unittest

from benchmark import restitution as r


class EconomicChoiceTests(unittest.TestCase):
    def setUp(self):
        self.rows = [dict(attempt_id='first', configuration_id='a', verdict='SATISFAIT',
                         requested_configuration={'model': 'vendor/a', 'effort': 'off'},
                         cost=dict(value='0.12', unit='USD', rank=2), detail_href='/first'),
                     dict(attempt_id='second', configuration_id='b', verdict='SATISFAIT',
                         requested_configuration={'model': 'vendor/b', 'effort': 'high'},
                         cost=dict(value='0.001', unit='USD', rank=1), detail_href='/second')]
        self.coverage = dict(planned_cells=2, attempted_cells=2, evaluated_attempts=2,
                             decided_attempts=2, not_started=0)

    def choice(self, rows=None, cases=1, pending=None):
        return r._economic_choice(self.rows if rows is None else rows, cases, self.coverage,
                                  [] if pending is None else pending)

    def test_only_cheapest_conforming_configuration_with_exact_observed_cost(self):
        before = deepcopy(self.rows)
        value = self.choice()
        self.assertEqual('vendor/b', value['configuration']['model'])
        self.assertEqual('high', value['configuration']['effort'])
        self.assertEqual(('0.001', 'USD', 2), (value['amount'], value['unit'], value['count']))
        self.assertEqual(before, self.rows)
        self.rows[1]['verdict'] = 'NE SATISFAIT PAS'
        self.assertIsNone(self.choice())

    def test_abstains_with_ties_unknown_cost_incomplete_work_or_repeated_attempts(self):
        for change in ('tie', 'unknown', 'pending', 'unstarted', 'multi_case', 'repeated', 'undecided'):
            rows = deepcopy(self.rows)
            coverage = deepcopy(self.coverage)
            pending, cases = [], 1
            if change == 'tie': rows[1]['cost']['value'] = '0.12'
            if change == 'unknown': rows[1]['cost'].update(value=None, rank=None)
            if change == 'pending': pending = [{'attempt_id': 'pending'}]
            if change == 'unstarted': coverage.update(planned_cells=3, not_started=1)
            if change == 'multi_case': cases = 2
            if change == 'repeated': rows[1]['configuration_id'] = 'a'
            if change == 'undecided': coverage['decided_attempts'] = 1
            with self.subTest(change=change):
                self.assertIsNone(r._economic_choice(rows, cases, coverage, pending))
