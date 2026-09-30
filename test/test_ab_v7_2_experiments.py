"""Focused integration tests; no performance campaign or new theory claim.

Run: python -m unittest discover -s test -p test_ab_v7_2_experiments.py -v
"""
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from experiments.ab_v7_2.data import (ROOT, digest, make_case, mechanism_cases,
    save_dataset, load_dataset, generate_cases)
from experiments.ab_v7_2.adapters import ABLATIONS
from experiments.ab_v7_2.runner import execute, run
from experiments.ab_v7_2.report import summarize


class ExperimentTests(unittest.TestCase):
    def test_exact_scaling_and_tamper_detection(self):
        case = make_case('rational',[(1,3,4,'1/3')],1,['0','1/2','1','3/2'],
                         capacity='5/6',release_floor='1/6',background_power='1/6')
        self.assertEqual(case['model']['energy_scale'],6)
        self.assertEqual(case['model']['tasks'][0]['power'],2)
        self.assertEqual(case['model']['beta'],[0,3,6,9])
        self.assertEqual(case['model']['capacity'],5)
        with self.assertRaises(ValueError):
            make_case('float',[(1,2,3,0.5)],1,[0,1,2])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'data.json'
            save_dataset(path,[case],{})
            self.assertEqual(load_dataset(path)['cases'][0],case)
            raw = json.loads(path.read_text())
            raw['cases'][0]['model']['beta'][1] += 1
            path.write_text(json.dumps(raw))
            with self.assertRaises(ValueError):
                load_dataset(path)

    def test_holdout_preserves_arbitrary_priority_and_known_difference(self):
        case = mechanism_cases()[0]
        self.assertEqual([t['T'] for t in case['model']['tasks']],[10,9,7])
        seq = execute(case,'seq',20)
        analytic = execute(case,'v72_analytic',20)
        flow = execute(case,'v72_flow',20)
        self.assertEqual(seq['response_bounds'],[2,5,6],seq)
        self.assertEqual(analytic['status'],'NO_CERTIFICATE',analytic)
        self.assertEqual(flow['response_bounds'],[2,5,6],flow)

    def test_ablation_known_mechanism(self):
        case = mechanism_cases()[1]
        expected = {'full','no_first_work','last_block_only'}
        for method in ABLATIONS:
            result = execute(case,method,20)
            success = method.removeprefix('ablation_') in expected
            self.assertEqual(result['status'],'CERTIFIED' if success else 'NO_CERTIFICATE',result)
            self.assertEqual(result['response_bounds'],[3,5,7] if success else None,result)
            self.assertFalse(result['config']['production_rerun'])

    def test_full_audit_agrees_with_production_for_nonzero_floor_and_background(self):
        case = make_case('floor',[(1,3,4,1),(1,4,5,1)],2,[0,3,6,9,12],
                         capacity=4,release_floor=1,background_power=1)
        production = execute(case,'v72_analytic',20)
        audit = execute(case,'ablation_full',20)
        self.assertEqual(production['status'],'CERTIFIED',production)
        self.assertEqual(audit['status'],production['status'],audit)
        self.assertEqual(audit['response_bounds'],production['response_bounds'])
        self.assertEqual(audit['first_bounds'],production['first_bounds'])

    def test_unsupported_model_and_hard_timeout(self):
        unsupported = execute(mechanism_cases()[1],'seq',20)
        self.assertEqual(unsupported['status'],'NOT_APPLICABLE')
        timeout = execute(mechanism_cases()[0],'v72_flow',0.001)
        self.assertEqual(timeout['status'],'TIMEOUT')
        self.assertIsNone(timeout['response_bounds'])
        self.assertIsNone(timeout['analysis_wall_seconds'])
        self.assertTrue(timeout['censored'])

    def test_resume_code_guard_and_no_duplicate_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root/'data.json'
            save_dataset(data,[mechanism_cases()[0]],{})
            output = root/'run'
            run(data,output,('seq',),timeout=20)
            original = (output/'results.jsonl').read_bytes()
            run(data,output,('seq',),timeout=20,resume=True)
            self.assertEqual((output/'results.jsonl').read_bytes(),original)
            with self.assertRaises(ValueError):
                run(data,output,('seq',),timeout=21,resume=True)
            with patch('experiments.ab_v7_2.runner.source_identity',return_value={'sha256':'changed'}):
                with self.assertRaises(ValueError):
                    run(data,output,('seq',),timeout=20,resume=True)
            self.assertTrue(summarize(output)['complete'])

    def test_report_does_not_turn_timeout_into_lost_certificate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root/'data.json'
            cases = [mechanism_cases()[0],make_case('another',[(1,4,10,2),(2,5,9,3),(1,6,7,1)],
                                                   1,[2*l for l in range(7)],cohort='mechanism')]
            save_dataset(data,cases,{})
            output = root/'run'
            def fixture(case,method,timeout):
                result = dict(case_id=case['case_id'],input_sha256=case['input_sha256'],
                    config={'method':method,'timing_family':'production'},analysis_wall_seconds=1,
                    status='CERTIFIED',response_bounds=[2,5,6],first_bounds=None)
                if method=='v72_analytic':
                    result.update(status='TIMEOUT' if case['name']=='another' else 'NO_CERTIFICATE',
                                  response_bounds=None)
                return result
            with patch('experiments.ab_v7_2.runner.execute',side_effect=fixture):
                run(data,output,('seq','v72_analytic'),timeout=20,repetitions=2)
            result = summarize(output)
            pair = result['pairs'][0]
            self.assertEqual(pair['a_only_vs_completed_b'],1)
            self.assertEqual(pair['unresolved_pairs'],1)
            seq = next(r for r in result['summaries'] if r['method']=='seq')
            self.assertEqual(seq['primary_cases'],2)
            self.assertEqual(seq['completed_timing_requests'],4)

    def test_legacy_generator_reproducible_and_energy_pairing(self):
        config = dict(seed=20260930,samples=1,processors=[2],tasks=[3],uc=['0.3'],ue=['0.5','0.8'],
            period_min=8,period_max=12,min_task_util='0.01',max_task_util='0.8',tolerance='0.05',
            system_config=str(ROOT/'system_config_unified_template.yml'),power_model='workload',
            harvest_rate='1',harvest_gaps=[1,2],capacities=[None])
        a,b = generate_cases(config),generate_cases(config)
        self.assertEqual(a,b)
        self.assertEqual(len(a),4)
        skeletons = {json.dumps([[t[k] for k in ('C','D','T')] for t in c['model']['tasks']]) for c in a}
        self.assertEqual(len(skeletons),1)
        for case in a:
            model = case['model']
            ue = sum(Fraction(t['C'],t['T'])*t['power']/model['energy_scale'] for t in model['tasks'])
            self.assertEqual(ue,Fraction(case['metadata']['target_ue']))

    def test_recursive_adapter_matches_old_rm_dispatcher(self):
        # Run legacy dispatcher in a separate interpreter, preserving its own imports.
        case = make_case('rm',[(1,4,5,1),(1,5,7,1)],2,[0,3,6,9,12,15])
        result = execute(case,'seq',20)
        code = '''
import json
from fractions import Fraction
from experiments.v9_3.rta_load_cross import _analyze_worker, core
payload = dict(tasks=(core.V93Task('1',1,4,5,1),core.V93Task('2',1,5,7,1)),
 beta=(0,3,6,9,12,15),e0='0',timeout=20,taskset_id='rm',method='SEQ',processors=2,
 target_uc='x',actual_uc='x',target_ue='x',actual_ue='x')
r = _analyze_worker(None,payload)
print('RESULT='+json.dumps([r['taskset_proven'],r['response_time_vector']]))
'''
        child = subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
        line = next(line for line in child.stdout.splitlines() if line.startswith('RESULT='))
        legacy = json.loads(line.removeprefix('RESULT='))
        self.assertTrue(legacy[0],child.stdout)
        self.assertEqual(result['response_bounds'],legacy[1])


if __name__ == '__main__':
    unittest.main()
