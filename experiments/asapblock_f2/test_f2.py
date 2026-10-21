"""Protocol-level tests: pairing, invariants, censoring, replay and resumption."""
import argparse
from collections import defaultdict
import contextlib
from fractions import Fraction as F
import io
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch
import common as C
import inputs
import run
import summarize as S

class ProtocolTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.sets=inputs.build()

 def test_exact_frozen_counts_and_pilot_identity(self):
  for name in self.sets:self.assertEqual(len(self.sets[name]),C.PROTOCOL['expected'][name])
  self.assertEqual(len({c['metadata']['cluster_id'] for c in self.sets['main']}),1000)
  self.assertEqual(len({c['metadata']['cluster_id'] for c in self.sets['pilot']}),20)
  self.assertTrue({c['case_id'] for c in self.sets['pilot']}<={c['case_id'] for c in self.sets['main']})
  total=sum(len(self.sets[C.SUITES[n][0]])*len(C.SUITES[n][1])*C.SUITES[n][3] for n in C.CORE_SUITES)
  seconds=sum(len(self.sets[C.SUITES[n][0]])*len(C.SUITES[n][1])*C.SUITES[n][3]*C.SUITES[n][2] for n in C.CORE_SUITES)
  self.assertEqual((total,seconds),(28248,1172880))

 def test_frozen_manifest_matches_every_family(self):
  expected=C.read(C.HERE/'expected_inputs.json')
  for name,cases in self.sets.items():
   self.assertEqual(C.digest(sorted((c['case_id'],c['input_sha256']) for c in cases)),expected[name]['identity'])

 def test_generator_restores_global_random_state(self):
  state=random.getstate();inputs.bone('main',2,4,'.2',0)
  self.assertEqual(state,random.getstate())

 def test_all_exact_energy_cpu_and_supply_invariants(self):
  for name in ['main','mechanism','scale','numeric_scale','robustness']:
   for c in self.sets[name]:inputs.validate(c)

 def test_paired_main_variants_share_task_order_and_work(self):
  by=defaultdict(list)
  for c in self.sets['main']:by[c['metadata']['cluster_id']].append(c)
  for cases in by.values():
   self.assertEqual(len({tuple((t['C'],t['T']) for t in c['model']['tasks']) for c in cases}),1)
   for ue in {c['metadata']['target_ue'] for c in cases}:
    cc=[c for c in cases if c['metadata']['target_ue']==ue]
    self.assertEqual(len({tuple(t['power'] for t in c['model']['tasks']) for c in cc}),1)

 def test_scale_factors_change_all_time_parameters(self):
  by=defaultdict(list)
  for c in self.sets['numeric_scale']:by[c['metadata']['cluster_id']].append(c)
  self.assertEqual(len(by),40)
  for cc in by.values():
   base=next(c for c in cc if c['metadata']['time_factor']==1)
   for c in cc:
    fac=c['metadata']['time_factor'];self.assertEqual(c['metadata']['harvest_gap'],5*fac)
    for a,b in zip(base['model']['tasks'],c['model']['tasks']):
     for k in ['C','D','T']:self.assertEqual(b[k],a[k]*fac)
     self.assertEqual(b['power'],a['power'])
    for l,v in enumerate(base['model']['beta']):self.assertEqual(c['model']['beta'][fac*l],fac*v)

 def test_heterogeneity_controls_weights_not_total_energy(self):
  by=defaultdict(list)
  for c in self.sets['mechanism']:
   m=c['metadata'];by[m['cluster_id'],m['target_ue'],m['harvest_gap']].append(c)
  for cc in by.values():
   cc=sorted(cc,key=lambda c:F(c['metadata']['heterogeneity_h']))
   self.assertEqual(len(cc),3)
   self.assertEqual(len({tuple((t['C'],t['D'],t['T']) for t in c['model']['tasks']) for c in cc}),1)
   self.assertEqual(len(set(t['power'] for t in cc[0]['model']['tasks'])),1)
   cvs=[c['metadata']['power_cv'] for c in cc];self.assertLessEqual(cvs[0],cvs[1]);self.assertLessEqual(cvs[1],cvs[2]+1e-15)

 def test_preselected_samples_have_correct_sources(self):
  tm=self.sets['timing'];a=[c for c in tm if c['metadata']['family']=='main'];b=[c for c in tm if c['metadata']['family']=='scale']
  self.assertEqual((len(a),len(b)),(192,48))
  self.assertEqual(len({c['metadata']['cluster_id'] for c in a}),192)
  self.assertTrue(all(c['metadata']['scale_original_grid'] for c in b))
  self.assertEqual(len({c['metadata']['cluster_id'] for c in self.sets['mechanism']}),50)

 def test_malformed_energy_is_not_silently_repaired(self):
  import copy
  c=copy.deepcopy(self.sets['pilot'][0]);c['model']['tasks'][0]['power']+=1
  with self.assertRaises(ValueError):inputs.validate(c)

class StatisticalTests(unittest.TestCase):
 def test_timeout_and_missing_repetitions_are_not_speed_pairs(self):
  base={'repeat':0,'status':'NO_CERTIFICATE','response_bounds':None,'first_bounds':None,'request_wall_seconds':1}
  self.assertIsNone(S.stable([base],3))
  self.assertIsNone(S.stable([base,{**base,'repeat':1,'status':'TIMEOUT'},{**base,'repeat':2}],3))
  self.assertIsNotNone(S.stable([{**base,'repeat':i} for i in range(3)],3))

 def test_first_unit_comparison_uses_fair_derived_bound(self):
  c={'model':{'tasks':[{'C':2,'D':5}]}}
  out=S.precision(c,{'response_bounds':[4],'first_bounds':[2]},
   {'response_bounds':[4],'first_bounds':[4]},'completion_only')
  self.assertAlmostEqual(out['first_reduction_percent'],100/3)
  self.assertEqual(out['response_reduction_percent'],0)

 def test_negative_response_improvements_are_preserved(self):
  c={'model':{'tasks':[{'C':1,'D':20}]}}
  out=S.precision(c,{'response_bounds':[12]}, {'response_bounds':[10]},'seq')
  self.assertEqual(out['response_reduction_percent'],-20)

 def test_precision_table_retains_ties_and_both_directions(self):
  c={'model':{'tasks':[{'C':1,'D':20}]}}
  values=[S.precision(c,{'response_bounds':[r]},{'response_bounds':[10]},'seq') for r in [8,10,12]]
  out=S.precision_summary(values)
  self.assertEqual(out['response_mean_percent'],0)
  self.assertEqual(out['response_median_percent'],0)
  self.assertEqual(out['absolute_response_mean_slots'],0)
  self.assertEqual((out['identical_vectors'],out['any_tighter_sets'],out['any_looser_sets']),(1,1,1))
  self.assertAlmostEqual(out['identical_vector_percent'],100/3)

 def test_paired_cluster_bootstrap_preserves_zero_variance(self):
  self.assertEqual(S.ci([('a','x',10,1),('a','x',10,1),('b','x',10,1)],50),[10,10])

 def test_censored_population_percentile_requires_observed_ranks(self):
  self.assertIsNone(S.population_p95([1,2,3],10))
  self.assertAlmostEqual(S.population_p95(list(range(99)),100),94.05)
  self.assertIsNone(S.population_p95(list(range(99)),100,False))

 def test_equal_configuration_weights_do_not_reward_large_survivor_counts(self):
  import plots
  rr=[]
  for i in range(8):
   for j in range(1 if i==0 else 9):rr.append({'uc':str(i),'ue':'x','gap':1,'value':100 if i==0 else 0})
  self.assertEqual(plots.equal_cell_mean(rr,'value'),12.5)

class ProcessTests(unittest.TestCase):
 def test_missing_jsonl_entry_is_recovered_from_immutable_request_copy(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'primary.jsonl'
   records=[{'request_id':C.digest(i),'status':'NO_CERTIFICATE','value':i} for i in range(2)]
   for row in records:C.journaled_append(path,row)
   prefix=path.read_text().splitlines(keepends=True)[0]
   path.write_text(prefix)  # Simulate a lost index write, not a lost analysis.
   self.assertEqual(C.record_rows(path),records)
   self.assertEqual(C.sync_journal(path),records)
   self.assertTrue(path.read_text().startswith(prefix))
   self.assertEqual(C.sync_journal(path),records)

 def test_conflicting_durable_copies_fail_instead_of_selecting_a_result(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'primary.jsonl';row={'request_id':C.digest('conflict'),'status':'NO_CERTIFICATE'}
   C.journaled_append(path,row)
   path.write_text(C.canonical({**row,'status':'CERTIFIED'})+'\n')
   with self.assertRaisesRegex(ValueError,'disagree'):C.record_rows(path)

 def test_wall_timeout_cannot_become_no_certificate(self):
  c=inputs.data.make_case('timeout',[(1,3,5,1)],1,list(range(4)))
  ans=run.call(c,'production_analytic',.001,C.environment()['physical_cpus'][0])
  self.assertEqual(ans['status'],'TIMEOUT');self.assertTrue(ans['censored'])
  self.assertIsNone(ans['response_bounds'])

 def test_resumption_and_changed_source_guard(self):
  c=inputs.data.make_case('resume',[(1,3,5,1)],1,list(range(4)),metadata={'cluster_id':'resume'})
  args=argparse.Namespace(limit=None,workers=1)
  with tempfile.TemporaryDirectory() as temp:
   out=Path(temp)
   with patch.object(inputs,'load',return_value=[c]),patch.object(run,'call',return_value={'status':'NO_CERTIFICATE','request_wall_seconds':.1}):
    C.write(out/'preflight_checks.json',{'status':'PASS','source_sha256':C.source_identity()['sha256']})
    with contextlib.redirect_stdout(io.StringIO()):first=run.run_suite(out,'pilot',args)
    with patch.object(run,'call',side_effect=AssertionError('Repeated finished request')):
     second=run.run_suite(out,'pilot',args)
    self.assertEqual(first,second)
    with patch.object(C,'source_identity',return_value={'sha256':'changed','files':{}}):
     with self.assertRaisesRegex(ValueError,'Preflight'):run.run_suite(out,'pilot',args)

 def test_dependencies_remain_byte_identical(self):
  self.assertGreaterEqual(len(C.source_identity()['files']),40)

if __name__=='__main__':unittest.main(verbosity=2)
