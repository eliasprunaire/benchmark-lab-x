"""The private advice balances observed quality without inventing a score"""
from copy import deepcopy
import unittest

from benchmark import restitution as r


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.quality = {'measure': 'Clarté', 'proof': 'Citation', 'unit': 'descriptif',
                        'scale': ['excellent', 'acceptable', 'faible'], 'favorable': 'excellent'}
        self.rows = [dict(attempt_id='first', configuration_id='a', verdict='SATISFAIT',
                         requested_configuration={'model': 'vendor/a', 'effort': 'off'},
                         cost=dict(value='0.001', unit='USD', rank=1),
                         measures=[dict(criterion_id='Q1', value='excellent', unit='descriptif',
                                        definition=self.quality, rank=1)], detail_href='/first'),
                     dict(attempt_id='second', configuration_id='b', verdict='SATISFAIT',
                         requested_configuration={'model': 'vendor/b', 'effort': 'high'},
                         cost=dict(value='0.12', unit='USD', rank=2),
                         measures=[dict(criterion_id='Q1', value='acceptable', unit='descriptif',
                                        definition=self.quality, rank=2)], detail_href='/second')]
        self.columns = [dict(id='cost'), dict(id='Q1', criterion_id='Q1',
                                             definition=self.quality)]
        self.coverage = dict(planned_cells=2, attempted_cells=2, evaluated_attempts=2,
                             decided_attempts=2, not_started=0)

    def choice(self, rows=None, cases=1, pending=None):
        return r._recommendation(self.rows if rows is None else rows, self.columns, cases,
                                 self.coverage, [] if pending is None else pending)

    def test_prefers_the_unique_quality_dominant_configuration(self):
        before = deepcopy(self.rows)
        value = self.choice()
        self.assertEqual('vendor/a', value['configuration']['model'])
        self.assertEqual('quality_then_cost', value['basis'])
        self.assertEqual(('0.001', 'USD', 2), (value['amount'], value['unit'], value['count']))
        self.assertEqual(before, self.rows)
        self.rows[1]['verdict'] = 'NE SATISFAIT PAS'
        self.assertIsNone(self.choice())

    def test_uses_cost_only_when_observed_quality_is_equal(self):
        self.rows[1]['measures'][0].update(value='excellent', rank=1)
        value = self.choice()
        self.assertEqual('vendor/a', value['configuration']['model'])
        self.assertEqual('equal_quality_cost', value['basis'])

    def test_abstains_with_ties_unknown_cost_incomplete_work_or_repeated_attempts(self):
        for change in ('tie', 'unknown', 'pending', 'unstarted', 'multi_case', 'repeated', 'undecided'):
            rows = deepcopy(self.rows)
            coverage = deepcopy(self.coverage)
            pending, cases = [], 1
            if change == 'tie':
                rows[1]['cost']['value'] = rows[0]['cost']['value']
                rows[1]['measures'][0].update(value='excellent', rank=1)
            if change == 'unknown': rows[1]['cost'].update(value=None, rank=None)
            if change == 'pending': pending = [{'attempt_id': 'pending'}]
            if change == 'unstarted': coverage.update(planned_cells=3, not_started=1)
            if change == 'multi_case': cases = 2
            if change == 'repeated': rows[1]['configuration_id'] = 'a'
            if change == 'undecided': coverage['decided_attempts'] = 1
            with self.subTest(change=change):
                self.assertIsNone(r._recommendation(rows, self.columns, cases, coverage, pending))

    def test_abstains_when_quality_criteria_trade_off(self):
        rows = deepcopy(self.rows)
        for row, ranks in zip(rows, ((1, 2), (2, 1))):
            row['measures'] = [dict(criterion_id='Q1', value='excellent' if ranks[0] == 1 else 'acceptable',
                                    unit='descriptif', definition=self.quality, rank=ranks[0]),
                               dict(criterion_id='Q2', value='excellent' if ranks[1] == 1 else 'acceptable',
                                    unit='descriptif', definition=self.quality, rank=ranks[1])]
        columns = [dict(id='cost'), dict(id='Q1', criterion_id='Q1', definition=self.quality),
                   dict(id='Q2', criterion_id='Q2', definition=self.quality)]
        self.assertIsNone(r._recommendation(rows, columns, 1, self.coverage, []))

    def test_prefers_better_quality_then_uses_cost_for_equal_quality(self):
        rows = deepcopy(self.rows)
        rows[0]['cost'].update(value='0.12', rank=2)
        rows[1]['cost'].update(value='0.001', rank=1)
        value = r._recommendation(rows, self.columns, 1, self.coverage, [])
        self.assertEqual('vendor/a', value['configuration']['model'])
        self.assertEqual('quality_then_cost', value['basis'])

    def test_closed_quality_scale_is_orderable_without_numeric_score(self):
        definition = {'measure': 'Clarté', 'proof': 'Citation', 'unit': 'descriptif',
                      'scale': ['excellent', 'acceptable', 'faible'], 'favorable': 'excellent'}
        metric = {'value': 'acceptable', 'unit': 'descriptif', 'definition': definition}
        self.assertTrue(r._orderable(definition))
        self.assertEqual(2, r._metric_number(metric))
