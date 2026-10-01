"""Campaign invariants: paired models, ranges, batching and censored results."""
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from experiments.ab_v7_2.data import ROOT
from experiments.ab_v7_2.campaign import settings,counts,generate,prepare,campaign_summary
from experiments.ab_v7_2.runner import run

class CampaignTests(unittest.TestCase):
    def config(self):
        return json.loads((ROOT/'configs/ab_v7_2/qualification.json').read_text())

    def small(self):
        cfg=self.config()
        cfg['defaults'].update(processors=[2],task_multipliers=[2],uc=['0.5'],samples=40,batch_size=2)
        cfg['stages']={'qualification':{'seed_offset':20}}
        return cfg

    def test_pairing_exact_energy_and_global_rng(self):
        cfg=self.small();cases=generate(cfg,'qualification',0,2)
        state=random.getstate()
        self.assertEqual(cases,generate(cfg,'qualification',0,2))
        self.assertEqual(state,random.getstate())
        self.assertEqual(len({c['metadata']['cluster_id'] for c in cases}),2)
        for c in cases:
            model=c['model'];tasks=model['tasks'];scale=model['energy_scale']
            cpu=sum(Fraction(t['C'],t['T']) for t in tasks)
            self.assertLessEqual(abs(cpu-1),Fraction('0.01'))
            energy=sum(Fraction(t['C']*t['power'],t['T']*scale) for t in tasks)
            self.assertEqual(energy,Fraction(c['metadata']['target_ue']))
            self.assertTrue(all(Fraction(t['D'],t['T'])>=Fraction('0.7') for t in tasks))
            self.assertEqual([t['T'] for t in tasks],sorted(t['T'] for t in tasks))

    def test_batches_are_disjoint_and_boundaries_fail(self):
        cfg=self.small()
        a,b=generate(cfg,'qualification',0,2),generate(cfg,'qualification',2,2)
        self.assertFalse({c['metadata']['cluster_id'] for c in a}&{c['metadata']['cluster_id'] for c in b})
        for start,count in ((-1,1),(39,2),(0,0)):
            with self.assertRaises(ValueError):generate(cfg,'qualification',start,count)

    def test_fixed_power_keeps_workload_coefficients_and_observes_energy_load(self):
        from experiments.v9_3.rta_load_cross import _load_exact_energy_model
        weights=_load_exact_energy_model(ROOT/'system_config_unified_template.yml')
        cfg=self.small();cfg['stages']['qualification'].update(energy_mode='fixed_scale',
            power_scales=['0.5','1'],ue=['0.7'],uc=['0.2','0.8'],deadline_modes=['implicit'])
        cases=generate(cfg,'qualification',0,2)
        self.assertEqual(len(cases),8)
        self.assertEqual(counts(cfg,'qualification',2)['inputs'],8)
        actuals=set()
        for c in cases:
            meta,m=c['metadata'],c['model'];self.assertIsNone(meta['target_ue'])
            powers=[Fraction(t['power'],m['energy_scale']) for t in m['tasks']]
            self.assertEqual(powers,[weights[w]*Fraction(meta['power_scale']) for w in meta['workloads']])
            actual=sum(Fraction(t['C'],t['T'])*p for t,p in zip(m['tasks'],powers))
            self.assertEqual(actual,Fraction(meta['actual_ue']))
            self.assertEqual(meta['mean_energy_overload'],actual>1);actuals.add(actual)
        self.assertGreater(len(actuals),2)
        cfg['stages']['qualification']['ue']=['0.4','0.8']
        with self.assertRaises(ValueError):settings(cfg,'qualification')

    def test_temporal_scaling_preserves_normalized_demands(self):
        cfg=self.small();cfg['stages']['qualification'].update(deadline_modes=['implicit'],ue=['0.4'],time_factors=[1,4])
        a,b=generate(cfg,'qualification',0,1)
        ma,mb=a['model'],b['model']
        for x,y in zip(ma['tasks'],mb['tasks']):
            self.assertEqual([y[k] for k in ('C','D','T')],[4*x[k] for k in ('C','D','T')])
            self.assertEqual(Fraction(x['power'],ma['energy_scale']),Fraction(y['power'],mb['energy_scale']))
        for i in range(len(ma['beta'])):
            self.assertEqual(Fraction(mb['beta'][4*i],mb['energy_scale']),4*Fraction(ma['beta'][i],ma['energy_scale']))

    def test_capacity_dm_and_latency_semantics(self):
        cfg=self.small();cfg['stages']['qualification'].update(ue=['0.4'],deadline_modes=['uniform_C_T'],
            priorities=['DM'],capacity_factors=['2'],supply_models=[dict(kind='rate_latency',latency=10)])
        c=generate(cfg,'qualification',0,1)[0];m=c['model'];ts=m['tasks'];scale=m['energy_scale']
        self.assertEqual([t['D'] for t in ts],sorted(t['D'] for t in ts))
        ref=sum(sorted((Fraction(t['power'],scale) for t in ts),reverse=True)[:2])
        self.assertEqual(Fraction(m['capacity'],scale),2*ref)
        self.assertEqual([Fraction(x,scale) for x in m['beta'][:12]],[0]*11+[1])
        cfg['stages']['qualification']['deadline_modes']=['arbitrary_D_gt_T']
        with self.assertRaises(ValueError):settings(cfg,'qualification')

    def test_large_integer_generator_and_log_periods(self):
        cfg=self.small();cfg['stages']['qualification'].update(processors=[16],task_multipliers=[8],
            uc=['0.1'],ue=['0.4'],deadline_modes=['implicit'],generator=dict(period_distribution='log_uniform',period_max=1000))
        cases=generate(cfg,'qualification',0,1);self.assertEqual(len(cases[0]['model']['tasks']),128)
        self.assertTrue(all(100<=t['T']<=1000 for t in cases[0]['model']['tasks']))
        self.assertEqual(counts(self.config(),'qualification')['requests'],576)

    def test_timeout_pairing_and_overlapping_batches_rejected(self):
        cfg=self.small();cfg['stages']['qualification'].update(deadline_modes=['implicit'],ue=['0.4'])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'config.json';config.write_text(json.dumps(cfg))
            for start in (0,2):
                out=root/f'b{start}';prepare(config,out,['qualification'],start=start,count=2)
                def fixture(case,method,timeout):
                    return dict(case_id=case['case_id'],input_sha256=case['input_sha256'],cohort=case['cohort'],name=case['name'],
                        config={'method':method,'timing_family':'production'},
                        status='TIMEOUT' if method=='seq' else 'NO_CERTIFICATE',
                        response_bounds=None,first_bounds=None,
                        analysis_wall_seconds=None if method=='seq' else .01,request_wall_seconds=.02)
                with patch('experiments.ab_v7_2.runner.execute',side_effect=fixture):
                    run(out/'qualification-dataset.json',out/'qualification',('seq','v72_analytic'),timeout=3)
            result=campaign_summary(root/'combined',[root/'b0',root/'b2'])
            self.assertEqual(result['pairs'][0]['unresolved'],4)
            self.assertEqual(result['pairs'][0]['a_only'],0)
            self.assertEqual(result['pairs'][0]['b_only'],0)
            self.assertTrue(all(c['certified_fraction_under_budget_ci95'] is None for c in result['cells']))
            with self.assertRaises(ValueError):campaign_summary(root/'bad',[root/'b0',root/'b0'])

if __name__=='__main__':unittest.main()
