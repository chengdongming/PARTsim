"""Reproduction checks using frozen identities and small verified models."""
import argparse
from collections import Counter, defaultdict
import contextlib
import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import prepare
import reproduce
import summarize


class InputIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.datasets = prepare.build_inputs()

    def test_all_archived_inputs_regenerate(self):
        self.assertEqual({k:len(v) for k,v in self.datasets.items()},
            {'main':1920,'latest':720,'constructed':180,'flow':576,'scale_all':390,
             'audit_timing':24,'quick_timing':24,'scale':18,'flow_timing':19})

    def test_cpu_draw_selections_use_original_hash_rules(self):
        by_cluster = defaultdict(list)
        for case in self.datasets['latest']:
            by_cluster[case['metadata']['cluster_id']].append(case)
        salt = 'ASAPBlock serial costs 20261014 predetermined 24 CPU draws'
        chosen = sorted(by_cluster,key=lambda key:reproduce.digest([salt,'draw',key]))[:24]
        ids = [min(by_cluster[key],key=lambda c:reproduce.digest([salt,'variant',c['case_id']]))['case_id']
               for key in chosen]
        self.assertEqual(ids,[c['case_id'] for c in self.datasets['audit_timing']])
        self.assertEqual(len({c['metadata']['cluster_id'] for c in self.datasets['audit_timing']}),24)
        self.assertEqual(len({c['metadata']['cluster_id'] for c in self.datasets['latest']}),60)

    def test_flow_timing_is_the_frozen_conditional_sample(self):
        items = reproduce.read(reproduce.HERE/'configs/selections.json')['flow_timing']
        self.assertEqual(Counter(item['category'] for item in items),
            Counter(common_certified=8,common_no_certificate=8,precision_gain_example=1,
                    delayed_common_certificate=1,large_timeout_example=1))
        self.assertTrue({item['case_id'] for item in items} <= {c['case_id'] for c in self.datasets['flow']})

    def test_runtime_matches_frozen_source_bytes(self):
        self.assertGreaterEqual(len(reproduce.read(reproduce.HERE/'runtime/frozen_sources.json')),34)
        self.assertIn('sha256',reproduce.source_identity())


class WorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cpu = min(os.sched_getaffinity(0))
        cls.family = prepare.constructed()[0]
        cls.simple = prepare.data.make_case('synthetic-single-task',[(1,4,5,1)],1,list(range(5)))

    def test_every_method_and_its_original_reference(self):
        methods = list(dict.fromkeys(method for spec in reproduce.SUITES.values() for method in spec['methods']))
        for method in methods:
            with self.subTest(method=method):
                result = reproduce.call(self.simple,method,10,'wall',self.cpu)
                self.assertEqual(result['status'],'CERTIFIED',result)
                answer = result['certificate'] if method not in ('seq','seq_audit') else result
                check = reproduce.call(self.simple,method,10,'wall',self.cpu,'reference',answer)
                self.assertEqual(check['status'],'PASS',check)

    def test_full_production_and_shared_kernel_agree_on_constructed_family(self):
        results = {method:reproduce.call(self.family,method,10,'wall',self.cpu)
                   for method in ('production_analytic','production_flow','full','completion_only','no_shared_budget')}
        for method in ('production_analytic','production_flow','full'):
            self.assertEqual(results[method]['status'],'CERTIFIED')
            self.assertEqual(results[method]['response_bounds'],[3,5,7])
        self.assertEqual(results['completion_only']['status'],'NO_CERTIFICATE')
        self.assertEqual(results['no_shared_budget']['status'],'NO_CERTIFICATE')

    def test_request_timeout_remains_unresolved(self):
        result = reproduce.call(self.simple,'production_analytic',.001,'wall',self.cpu)
        self.assertEqual(result['status'],'TIMEOUT')
        self.assertTrue(result['censored'])
        self.assertIsNone(result.get('response_bounds'))

    def test_cpu_budget_is_enforced_inside_the_analysis(self):
        cases = prepare.generator.generate(prepare.read(prepare.HERE/'configs/campaigns.json')['latest'],'main',0,1)
        result = reproduce.call(cases[0],'production_flow',.001,'cpu',self.cpu)
        self.assertEqual(result['status'],'TIMEOUT')
        self.assertEqual(result['limit_kind'],'CPU')


class RunnerTests(unittest.TestCase):
    def test_resume_does_not_repeat_finished_requests_and_rejects_changed_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            with contextlib.redirect_stdout(io.StringIO()):
                prepare.prepare(output)
                args = argparse.Namespace(limit=1,budget_seconds=None,methods=None,workers=1)
                first = reproduce.run_suite(output,'constructed',args)
                with patch.object(reproduce,'call',side_effect=AssertionError('Finished request reran')):
                    second = reproduce.run_suite(output,'constructed',args)
                self.assertEqual(first,second)
                self.assertEqual(len(first),7)
                reproduce.verify(output,['constructed'])
                result = summarize.summarize(output,['constructed'])
                self.assertEqual(result[0]['reference_checks_pending'],0)
                self.assertTrue(result[0]['custom_run'])
                self.assertTrue((output/'constructed/tables/paired_counts.csv').exists())
                args.budget_seconds = 1
                with self.assertRaisesRegex(ValueError,'Resume refused'):
                    reproduce.run_suite(output,'constructed',args)

    def test_unresolved_and_repeated_statuses_are_not_speed_pairs(self):
        result = summarize.stable_cases([
            {'case_id':'a','method':'full','status':'CERTIFIED','response_bounds':[2],'request_wall_seconds':1},
            {'case_id':'a','method':'full','status':'TIMEOUT','response_bounds':None,'request_wall_seconds':15},
        ])
        self.assertEqual(result['a']['full']['status'],'UNSTABLE')

    def test_fair_completion_only_first_bound_uses_R_minus_C_plus_one(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            (output/'inputs').mkdir()
            folder = output/'ablation'
            folder.mkdir()
            case = prepare.data.make_case('synthetic-F-check',[(2,4,5,1)],1,list(range(5)),
                metadata={'cluster_id':'draw','stage':'synthetic','target_uc':'2/5'})
            reproduce.write(output/'inputs/latest.json',{'cases':[case]})
            reproduce.write(folder/'plan.json',{'dataset':'latest','selected_inputs':1,
                'methods':['full','completion_only'],'custom_run':True})
            for method,first in [('full',1),('completion_only',3)]:
                reproduce.append(folder/'final.jsonl',{'request_id':method,'case_id':case['case_id'],
                    'method':method,'status':'CERTIFIED','response_bounds':[3],'first_bounds':[first],
                    'request_wall_seconds':1,'stage':'synthetic','m':1,'n':1})
            summarize.summarize_suite(output,'ablation')
            with (folder/'tables/first_unit_by_input.csv').open(encoding='utf-8-sig') as stream:
                entries = list(csv.DictReader(stream))
            self.assertAlmostEqual(float(entries[0]['reference_F_reduction_percent']),50)


if __name__ == '__main__':
    unittest.main(verbosity=2)
