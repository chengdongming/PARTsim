"""Reviewed AN bounds, shared experiment contract and finite native audit."""
import copy
import json
import os
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from experiments.same_slot import an_rta, an_rta_bounds, an_replay, campaign

ROOT = Path(__file__).resolve().parents[1]


def case_for(tasks, M, B, e0=0, scale=1):
    model = dict(tasks=[dict(name=str(i), C=C, D=D, T=T, power=p)
                        for i, (C,D,T,p) in enumerate(tasks)],
                 processors=M, beta=list(B), release_floor=e0, capacity=None,
                 background_power=0, priority="INPUT_ORDER",
                 harvest_order="HARVEST_BEFORE_DEBIT", release_floor_stage="PRE_HARVEST",
                 energy_scale=scale)
    return dict(name="an-fixture", model=model, input_sha256=campaign.digest(model),
                case_id=campaign.digest(model), source_case_id="test", metadata={},
                cohort="test")


def periodic_beta(pattern, dmax):
    return [min(sum(pattern[(a+j)%len(pattern)] for j in range(L))
                for a in range(len(pattern))) for L in range(dmax+1)]


class ANRTATests(unittest.TestCase):
    def test_representative_cases_and_certificates(self):
        data = [
            ([(1,3,4,2),(1,5,5,1)], 2, [1], [[3,3],[2,2]]),
            ([(1,2,4,3),(1,6,6,2),(1,7,7,2)], 2, [2,3], [[2,2],[2,2],[3,3]]),
            ([(1,4,5,5),(1,8,8,3),(1,9,9,3),(1,10,10,3),(1,11,11,3)],
             2, [3,4], [[4,4],[2,2],[4,4],[5,5],[6,6]]),
            ([(1,3,4,5),(2,10,10,3),(1,11,11,2),(1,12,12,2)],
             2, [4], [[3,3],[2,4],[5,5],[5,5]]),
            ([(1,2,4,2),(1,8,8,2)], 2, [1], [[2,2],[6,6]])]
        for idx, (tasks,M,pattern,expected) in enumerate(data):
            case = case_for(tasks,M,periodic_beta(pattern,max(t[1] for t in tasks)))
            for method in an_rta.METHODS:
                with self.subTest(case=idx, method=method):
                    result = an_rta.analyze_case(case, method=method)
                    if idx==3 and method==an_rta.MR:
                        self.assertEqual(result["status"],"NO_CERTIFICATE")
                        self.assertIsNone(result["certificate"])
                    else:
                        self.assertEqual(result["status"],"CERTIFIED")
                        cert = result["certificate"]
                        self.assertEqual(cert["profile"], expected)
                        self.assertEqual(cert["method"], method)
                        self.assertEqual(an_rta.verify_certificate(case, json.loads(json.dumps(cert)))["status"],"PASS")
                        wrong = copy.deepcopy(cert)
                        wrong["response_bounds"][0] += 1
                        with self.assertRaises(ValueError):
                            an_rta.verify_certificate(case,wrong)

    def test_arbitrary_beta_and_single_task_boundary(self):
        for method in an_rta.METHODS:
            one=case_for([(1,2,3,3)],1,[0,0,3])
            self.assertEqual(an_rta.analyze_case(one,method=method)["certificate"]["response_bounds"],[2])
            no=case_for([(1,2,3,3)],1,[0,0,2])
            self.assertEqual(an_rta.analyze_case(no,method=method)["status"],"NO_CERTIFICATE")
            reserve=case_for([(1,1,3,5)],1,[0,2],3)
            self.assertEqual(an_rta.analyze_case(reserve,method=method)["certificate"]["response_bounds"],[1])
            # Valid weak (not superadditive) table must not be rejected.
            sufficient=case_for([(1,3,4,2),(1,4,5,1)],2,[0,4,4,4,4])
            self.assertEqual(an_rta.analyze_case(sufficient,method=method)["certificate"]["response_bounds"],[1,1])

    def test_input_contract_and_wrong_method(self):
        c=case_for([(1,1,2,3)],1,[0,3])
        for k,v in (("harvest_order","DEBIT_BEFORE_HARVEST"),("release_floor_stage","POST_HARVEST"),
                    ("capacity",100),("background_power",1),("priority","RM"),
                    ("energy_scale",1.5),("release_floor",-1),("beta",[0,2.5])):
            wrong=copy.deepcopy(c);wrong["model"][k]=v
            with self.subTest(field=k),self.assertRaises(ValueError):
                an_rta.analyze_case(wrong)
        with self.assertRaises(ValueError):
            an_rta.analyze_case(c, method="RTA-AB-OPT")
        cert=an_rta.analyze_case(c)["certificate"]
        wrong=copy.deepcopy(c)
        wrong["model"]["beta"]=[0,4]
        with self.assertRaises(ValueError):
            an_rta.verify_certificate(wrong,cert)

    def test_pattern_frequency_strict_and_resource_limit(self):
        tasks=((1,3,4,5),(2,10,10,3),(1,11,11,2),(1,12,12,2))
        case=case_for(tasks,2,periodic_beta([4],12))
        X=((3,3),(2,4),(5,5),(5,5))
        values=[an_rta_bounds.energy_bound(tasks,2,0,X,1,3,2,0,2,m)
                for m in an_rta.METHODS]
        self.assertEqual(values,[12,12,13])
        an_rta.clear()
        with patch.object(an_rta_bounds,"DP_STATE_LIMIT",0):
            result=an_rta.analyze_case(case,method=an_rta.OPT)
        self.assertEqual(result["status"],"RESOURCE_LIMIT")
        self.assertIsNone(result["certificate"])

    def test_branch_order_and_internal_maximization(self):
        rng=random.Random(28)
        for i in range(220):
            M=rng.randint(1,4)
            tasks=tuple((C:=rng.randint(1,min(T,3)),rng.randint(C,T),T,rng.randint(1,8))
                        for T in sorted(rng.randint(3,10) for _ in range(rng.randint(1,5))))
            X=tuple((R if C==1 else rng.randint(1,R),R) for C,D,T,p in tasks for R in [rng.randint(C,D)])
            k=rng.randrange(len(tasks));q=rng.choice([1,tasks[k][0]])
            w=rng.randint(q,tasks[k][1]);b=rng.randrange(w);z=rng.randint(0,min(q-1,b));h=rng.randint(0,b)
            values=[an_rta_bounds.energy_bound(tasks,M,k,X,q,w,b,z,h,m)
                    for m in an_rta.METHODS]
            self.assertLessEqual(values[0],values[1]);self.assertLessEqual(values[1],values[2])
            for method,value in zip(an_rta.METHODS[1:],values[1:]):
                self.assertEqual(value,an_rta_bounds.energy_bound(
                    tasks,M,k,X,q,w,b,z,h,method,enumerate_u=True))
            if i%20==0:an_rta.clear()
        an_rta.clear()


class ANReplayTests(unittest.TestCase):
    def test_exact_bypass_trace_and_first_deadline_miss(self):
        c=case_for([(1,3,4,2),(1,5,5,1)],2,[0,1,2,3,4,5])
        cert=an_rta.analyze_case(c)["certificate"]
        r=an_replay.replay(c,[1]*5,[[[0,1]],[[0,1]]],0,cert)
        self.assertEqual(r["slots"][0]["selected"],[[1,0]])
        self.assertEqual(r["slots"][2]["selected"],[[0,0]])
        self.assertEqual(r["max_observed_response"],[3,1])
        self.assertIsNone(r["certificate_violation"])
        c["model"]["tasks"][0]["D"]=2
        r=an_replay.replay(c,[1]*5,[[[0,1]],[[0,1]]])
        self.assertEqual(r["first_deadline_miss"],dict(task=0,name="0",release=0,boundary=2,remaining=1))

    def test_invalid_finite_trace_and_release_floor(self):
        c=case_for([(1,1,3,5)],1,[0,2],3)
        with self.assertRaises(ValueError):
            an_replay.replay(c,[2,2,2],[[[0,1]]],0)
        with self.assertRaises(ValueError):
            an_replay.replay(c,[1,2,2],[[[0,1]]],3)
        with self.assertRaises(ValueError):
            an_replay.replay(c,[2,2,2],[[[0,1],[1,1]]],3)

    def test_native_four_finite_trajectories(self):
        binary=Path(os.getenv("PARTSIM_TEST_BINARY",ROOT/"build/rtsim/rtsim")).resolve()
        if not binary.is_file():
            self.skipTest("rebuild PARTsim or set PARTSIM_TEST_BINARY")
        scale=250000
        rows=[
            ([(1,3,4,2),(1,5,5,1)],2,[1]*12,0,[0,0],8),
            ([(1,3,4,5),(2,10,10,3),(1,11,11,2),(1,12,12,2)],
             2,[4]*14,0,[0,1,0,0],2),
            ([(1,1,3,5)],1,[2]*10,3,[0],7),
            ([(1,2,4,3),(1,6,6,2),(1,7,7,2)],2,[2,3]*8,0,[0,1,2],8)]
        with tempfile.TemporaryDirectory() as td:
            for idx,(tasks,M,H,e0,offsets,cutoff) in enumerate(rows):
                with self.subTest(case=idx):
                    B=[93*x for x in periodic_beta(H,max(t[1] for t in tasks))]
                    case=case_for([(C,D,T,93*p) for C,D,T,p in tasks],M,B,93*e0,scale)
                    cert=an_rta.analyze_case(case,method=an_rta.OPT)["certificate"]
                    self.assertIsNotNone(cert)
                    audit=an_replay.run_native_periodic(case,[93*x for x in H],93*e0,
                        offsets,cutoff,binary,Path(td)/str(idx),cert)
                    self.assertEqual(audit["status"],"PASS_FINITE_TRACE")
                    self.assertEqual(audit["clipped_energy_j"],0)
                    native=json.loads((Path(td)/str(idx)/"trace.json").read_text())
                    ref=json.loads((Path(td)/str(idx)/"reference.json").read_text())
                    self.assertIsNone(ref["certificate_violation"])
                    modified=copy.deepcopy(native)
                    modified["same_slot_energy_summary"]["clipped_energy_j"]=1e-5
                    with self.assertRaises(ValueError):
                        an_replay.audit_native_trace(case,modified,ref)
                    modified=copy.deepcopy(native)
                    decision=next(e for e in modified["events"] if e["event_type"]=="scheduler_decision"
                                  and e["selected_jobs"])
                    decision["selected_jobs"]=[]
                    with self.assertRaises(ValueError):
                        an_replay.audit_native_trace(case,modified,ref)


class FourCoreTenTests(unittest.TestCase):
    def test_reproducible_fourcore10_generation_and_plan(self):
        from fractions import Fraction
        from experiments.same_slot import fourcore10
        config=fourcore10.canonical_config(42,1,["1/10","1/2"],["2/5","3/5"],40,200,"3",1)
        with tempfile.TemporaryDirectory() as td:
            output=Path(td)
            first=fourcore10.prepare(output,config)
            self.assertEqual(first,fourcore10.prepare(output,config))
            data=fourcore10.load(output/"inputs/fourcore10.json")
            self.assertEqual(len(data["cases"]),4)
            for c in data["cases"]:
                model=c["model"];tasks=model["tasks"]
                self.assertEqual(model["processors"],4)
                self.assertEqual(len(tasks),10)
                self.assertTrue(all(t["D"]==(9*t["T"]+9)//10 for t in tasks))
                self.assertEqual([t["T"] for t in tasks],sorted(t["T"] for t in tasks))
                self.assertLessEqual(abs(sum(Fraction(t["C"],t["T"]) for t in tasks)/4
                    -Fraction(c["metadata"]["target_uc"])),Fraction(1,400))
                self.assertEqual(sum(Fraction(t["C"]*t["power"],t["T"]*model["energy_scale"])
                    for t in tasks)/3,Fraction(c["metadata"]["target_ue"]))
            self.assertEqual([[t[x] for x in ("C","D","T")] for t in data["cases"][0]["model"]["tasks"]],
                             [[t[x] for x in ("C","D","T")] for t in data["cases"][1]["model"]["tasks"]])
            plan,cases=campaign.build_plan(output,"joint_fourcore10",limit=2,timeout=10,workers=1)
            self.assertEqual(plan["requests"],12)
            self.assertEqual(plan["methods"],list(campaign.FORMAL_METHODS))
            self.assertEqual(len(cases),2)
            path=output/"inputs/fourcore10.json"
            data["cases"].pop()
            data["dataset_sha256"]=campaign.digest({k:v for k,v in data.items() if k!="dataset_sha256"})
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                fourcore10.load(path)


class ANCampaignTests(unittest.TestCase):
    def test_all_six_methods_and_pairing(self):
        old=campaign.frozen_data().make_case("small",[(1,3,3,2),(1,4,4,1)],2,
                                             [4*i for i in range(5)],release_floor=0)
        case=campaign.convert(old)
        rows={}
        for method in campaign.FORMAL_METHODS:
            value=campaign.execute(case,method,10)
            self.assertEqual(value["status"],"CERTIFIED",value)
            rows[(case["case_id"],method)]=value
        summaries,pairs=campaign.summarize([case],campaign.FORMAL_METHODS,rows)
        self.assertEqual(len([r for r in summaries if r["cell"]=="ALL"]),6)
        allpairs=[r for r in pairs if r["cell"]=="ALL"]
        self.assertEqual(len(allpairs),15)
        self.assertEqual(sum(r["comparison_scope"]=="SAME_SCHEDULER" for r in allpairs),6)
        self.assertEqual(rows[(case["case_id"],"RTA-AN-OPT")]["first_bounds"],[1,1])
        for name in ("an_coverage","joint_coverage","an_frequency"):
            self.assertIn(name,campaign.METHODS)

    def test_worker_resource_and_timeout_states(self):
        c=case_for([(1,3,4,2),(1,5,5,1)],2,[0,1,2,3,4,5])
        with patch("experiments.same_slot.campaign.subprocess.run",
                   side_effect=campaign.subprocess.TimeoutExpired("worker",.01)):
            row=campaign.execute(c,an_rta.OPT,.01)
        self.assertEqual(row["status"],"TIMEOUT")
        rows={(c["case_id"],an_rta.OPT):row,
              (c["case_id"],an_rta.ANA):dict(status="RESOURCE_LIMIT")}
        summary,pairs=campaign.summarize([c],[an_rta.OPT,an_rta.ANA],rows)
        self.assertEqual(sum(r["resource_limit"] for r in summary if r["cell"]=="ALL"),1)
        self.assertEqual(pairs[-1]["unresolved"],1)


if __name__=="__main__":
    unittest.main()
