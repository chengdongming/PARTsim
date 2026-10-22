"""Boundary legality, old-bone identity, recovery and cohort separation checks."""
import copy
from fractions import Fraction as F
import io
import contextlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import ab_rta_f2_edges as E

# Eight anchor identities read from the completed original 9,600-input plan.
OLD_ANCHORS = [
 ['b47951e7e28f762691e9fcdaf5cdacf7f0c8e4fc0172bedb24f069ea96f5ad65','7dfa7e8d08dcd556fbcf6f85c4e0aa002a24e5ec73f51cfab2eac5bb2cc2e632'],
 ['c0ebc97fbb4823dc9804ad723961897c481be11490efbc78ddd458bcc128514b','dd59dcbc224de24358891667f6915d7cb0cb915f3ddde2746960556143d445da'],
 ['207df481b957991d01335dd592e53b8a4d3fe2d463f0c908eb52d04a4da3ae2c','ea97cf11d55ace77324b4ed5ba4f4387707415c6804cfbfbbf29abb5b72d1943'],
 ['193528c408305ae5059f1fface65a3f96459a36bddfde4d36b8b218c84f2251d','405c47d9db789b64464cacac9eec4fb8b8f88a6efec757f8d431a5f28f3750c6'],
 ['5006f9e07bcc510c9105c8592dcc168a90cf30b8415587cfe83857151a3f18eb','a60639e939e9653e56ea6be5660ddcceb8b25c9ae66372657ce583ef4edb1fa9'],
 ['916fcc2667f8e5f7e299effa1f3918a794a564b9729cd631fde00c7b16343231','c0cb6065c642506940d2a414dbd04432f0ef78b92660fef7ee54e7c36358a3c5'],
 ['7107c13bd8472beedccb3de40c2bcc1a283902ba8a85f92ccfffc9734dc8ef4c','dbd2e56c9e733d025510401b7b9edd40a7dcabde8979676cef714409f56815c0'],
 ['c2abc1ed3a2cc606d8f511c978e3313317ee9965d6b07147895d8658ffdb1238','397235e6ab1a236cee87bdf691bfaf1d2ecbcc5400881b518be9a2ab4203fe28']]


class EdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.C,cls.I,cls.R,cls.S=E.load_core(Path(__file__).resolve().parents[1])
        cls.cases,cls.manifest=E.build_cases(cls.C,cls.I,{'selected':OLD_ANCHORS},samples=1)

    def test_zero_is_rejected_and_one_is_allowed(self):
        for value in ['0','-0.01','1.01']:
            with self.assertRaises(ValueError):E.positive_target(value)
        self.assertEqual(E.positive_target('.05'),F(1,20))
        self.assertEqual(E.positive_target('1'),1)

    def test_original_model_rejects_zero_work_and_zero_power(self):
        for row in [[0,10,10,1],[1,10,10,0]]:
            with self.assertRaises(ValueError):self.I.data.make_case('zero',[row],1,list(range(11)))

    def test_new_grid_counts_and_original_anchor_identities(self):
        self.assertEqual(len(self.cases),32)
        self.assertEqual(len({c['case_id'] for c in self.cases}),32)
        self.assertEqual(self.manifest['anchored_old_cases'],8)
        self.assertEqual({c['metadata']['family'] for c in self.cases},{'boundary_cpu','boundary_energy'})

    def test_all_actual_utilizations_stay_inside_the_positive_domain(self):
        for c in self.cases:
            self.I.validate(c)
            self.assertTrue(0<F(c['metadata']['actual_uc'])<=1)
            self.assertTrue(0<F(c['metadata']['actual_ue'])<=1)
            self.assertTrue(all(t['C']>=1 and t['power']>0 for t in c['model']['tasks']))
        self.assertTrue(any(F(c['metadata']['actual_ue'])==1 for c in self.cases))

    def test_overshoot_rejection_reseeds_without_clipping_work(self):
        calls=[]
        def bone(family,m,n,uc,index):
            calls.append(index)
            return {'tasks':[{'C':101 if len(calls)==1 else 99,'T':100}],'index':index}
        b,rejected=E.upper_bounded_bone(SimpleNamespace(bone=bone),1,'1',3)
        self.assertEqual(calls,[3,103]);self.assertEqual(rejected,1)
        self.assertEqual(b['tasks'][0]['C'],99)

    def test_smoke_covers_both_axes_both_endpoints_and_both_m(self):
        selected=E.smoke_selection(self.C,self.cases)
        self.assertEqual(len(selected),8)
        keys={(c['metadata']['family'],c['metadata']['target_uc'] if c['metadata']['family']=='boundary_cpu' else c['metadata']['target_ue'],c['model']['processors']) for c in selected}
        self.assertEqual(len(keys),8)
        self.assertEqual(selected,E.smoke_selection(self.C,list(reversed(self.cases))))

    def test_original_directory_and_ancestors_cannot_be_output(self):
        for p in ['/base','/base/results','/']:
            with self.assertRaises(RuntimeError):E.disjoint('/base',p)
        E.disjoint('/base','/edges')

    def test_frozen_configuration_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'freeze.json';E.write(p,{'sha':'first'},immutable=True);before=p.read_bytes()
            with self.assertRaises(RuntimeError):E.write(p,{'sha':'other'},immutable=True)
            self.assertEqual(p.read_bytes(),before)

    def journal(self,tmp):
        path=Path(tmp)/'primary.jsonl';rows=[{'request_id':str(i)*64,'status':'NO_CERTIFICATE'} for i in [1,2]]
        for row in rows:E.write(path.with_suffix('.records')/(row['request_id']+'.json'),row)
        return path,rows

    def test_torn_final_line_is_recovered_and_original_bytes_backed_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,rows=self.journal(tmp);raw=(E.canonical(rows[0])+'\n{"request_id":').encode();path.write_bytes(raw)
            self.assertTrue(E.repair_journal(self.C,path))
            self.assertEqual([__import__('json').loads(v) for v in path.read_text().splitlines()],rows)
            self.assertEqual(next(path.parent.glob('*.interrupted_*')).read_bytes(),raw)
            self.assertFalse(E.repair_journal(self.C,path))

    def test_conflicting_or_middle_corruption_is_not_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,rows=self.journal(tmp)
            for raw in [(E.canonical({**rows[0],'status':'CERTIFIED'})+'\n{bad').encode(),b'{bad\n{bad']:
                path.write_bytes(raw)
                with self.assertRaises(RuntimeError):E.repair_journal(self.C,path)
                self.assertEqual(path.read_bytes(),raw)

    def row(self,cid):
        return {'case_id':cid,'cluster_id':'bone','m':2,'n':4,'uc':'1/2','ue':'2/5',
            'deadline':'implicit','gap':1,'method':'seq','states':['NO_CERTIFICATE']}

    def test_augmented_curves_reject_duplicate_input_or_duplicate_cell(self):
        base={'inputs':1,'repeat_stability':[self.row('a')],'precision':[]}
        for cid in ['a','b']:
            edge={'inputs':1,'repeat_stability':[self.row(cid)],'precision':[]}
            with self.assertRaises(RuntimeError):E.merge_plot_data(base,edge,{})

    def test_augmented_curves_leave_old_statistics_untouched(self):
        base={'inputs':1,'repeat_stability':[self.row('a')],'precision':[],'methods':[{'certified':7}]}
        edge={'inputs':1,'repeat_stability':[{**self.row('b'),'uc':'1/20'}],'precision':[]}
        before=copy.deepcopy(base);out=E.merge_plot_data(base,edge,{'base':'fixed'})
        self.assertEqual(base,before);self.assertEqual(len(out['rows']),2)
        self.assertEqual(out['old_inputs'],1);self.assertEqual(out['new_inputs'],1)

    def test_analysis_100_percent_does_not_claim_verification_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);E.write(root/'run_state.json',{'state':'RUNNING','stage':'INDEPENDENT_VERIFICATION','planned_requests':1})
            (root/'edges').mkdir();(root/'edges/primary.jsonl').write_text(E.canonical({'status':'CERTIFIED'})+'\n')
            with patch.object(E,'active',return_value=True):s=E.status(root)
            self.assertEqual(s['percent'],100);self.assertEqual(s['state'],'RUNNING')
            with patch.object(E,'active',return_value=False):self.assertEqual(E.status(root)['state'],'INTERRUPTED')

    def test_slim_pack_retains_records_but_omits_large_original_certificates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'edges';E.write(root/'run_state.json',{'state':'COMPLETE'})
            E.write(root/'edges/summary.json',{'done':True});E.write(root/'edges/certificates/a.json',{'preserve':True})
            with patch.object(E,'active',return_value=False),contextlib.redirect_stdout(io.StringIO()):p=E.pack(root)
            with zipfile.ZipFile(p) as z:
                self.assertIsNone(z.testzip());self.assertIn('edges/summary.json',z.namelist())
                self.assertNotIn('edges/certificates/a.json',z.namelist())
            self.assertTrue((root/'edges/certificates/a.json').exists())

    def test_core_source_identity_is_unchanged(self):
        self.assertEqual(self.C.source_identity()['sha256'],E.CORE_SOURCE)
        self.assertEqual(E.plan()['new_analysis_requests'],6400)


if __name__=='__main__':unittest.main(verbosity=2)
