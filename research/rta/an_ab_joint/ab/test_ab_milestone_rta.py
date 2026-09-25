"""Independent traces and allocation checks for the AB analytic refinement."""
from collections import deque
from copy import deepcopy
from itertools import product
import random, unittest
import ab_milestone_rta as ap


def age_scan(c, period, first, full, length):
    if not length:return 0
    def arrivals(span):return sum(min(c,span-r) for r in range(0,max(0,span),period))
    return min(length,max(min(length,c-int(a>=first),full-a)+arrivals(length-period+a) for a in range(full)))


def allocation_dp(tasks,m,profile,k,length,past,u,g,blocker):
    def work(i,span):
        t=tasks[i];return age_scan(t.wcet,t.period,*profile[i],span)
    limits=m*(past-u),(m-1)*u,m-1,m*g
    states={(0,0,0,0):0}
    for i,t in enumerate(tasks):
        if i==k:continue
        if i<k:
            choices=[]
            for n,s,cur,tail in product(range(past-u+1),range(u+1),range(2 if i<blocker else 1),range(g+1)):
                if (n+s<=work(i,past) and n+s+cur+int(i==blocker)<=work(i,past+1)
                    and n+s+cur+tail<=work(i,length) and tail<=work(i,length-past-1)):
                    choices.append(((n,s,cur,tail),(n+s+cur)*int(t.power)))
        else:choices=[((0,s,0,0),s*int(t.power)) for s in range(min(u,work(i,past))+1)]
        new={}
        for state,value in states.items():
            for counts,energy in choices:
                nxt=tuple(a+b for a,b in zip(state,counts))
                if all(a<=b for a,b in zip(nxt,limits)):new[nxt]=max(new.get(nxt,-1),value+energy)
        states=new
    values=[v for s,v in states.items() if s[-1]==m*g]
    return max(values)+u*int(tasks[k].power)+int(tasks[blocker].power) if values else None


def workload_trace_audit():
    cases=closed=checks=0
    for c in range(1,4):
        for period in range(c,6):
            for full in range(c,period+1):
                for first in ((full,) if c==1 else range(1,full+1)):
                    cases+=1;root=(0,0,0,False,(0,)*6);seen={root};queue=deque([root])
                    while queue:
                        cd,rem,age,started,bits=queue.popleft();closed+=1
                        for demand in (range(c+1) if cd==0 and rem==0 else (0,)):
                            dc,rr,aa,ss=(period,demand,0,False) if demand else (cd,rem,age,started)
                            if rr:
                                for length in range(1,7):
                                    count=(sum(bits[-(length-1):]) if length>1 else 0)+1
                                    assert count<=ap.workload(c,period,first,full,length),(c,period,first,full,length,bits)
                                    checks+=1
                            for execute in ((0,1) if rr else (0,)):
                                left=rr-execute
                                new_age,did_start=(aa+1,ss or bool(execute)) if left else (0,False)
                                new_bits=bits[1:]+(execute,)
                                for length in range(1,7):
                                    assert sum(new_bits[-length:])<=ap.workload(c,period,first,full,length)
                                    checks+=1
                                if left and (new_age>=full or (not did_start and new_age>=first)):continue
                                state=(max(0,dc-1),left,new_age,did_start,new_bits)
                                if state not in seen:seen.add(state);queue.append(state)
    return dict(cases=cases,closed_states=closed,inequalities=checks)


class MilestoneTests(unittest.TestCase):
    def test_closed_form_matches_age_scan(self):
        cases=0
        for c in range(1,6):
            for period in range(c,11):
                for full in range(c,period+1):
                    for first in ((full,) if c==1 else range(1,full+1)):
                        for length in range(25):
                            actual=ap.workload(c,period,first,full,length)
                            self.assertEqual(actual,age_scan(c,period,first,full,length))
                            relaxed=ap.workload(c,period,full,full,length)
                            old=((length+full-c)//period)*c+min(c,(length+full-c)%period)
                            self.assertEqual(relaxed,min(length,old));self.assertLessEqual(actual,relaxed);cases+=1
        print('closed_form_cases:',cases)

    def test_actual_and_virtual_trace_workload(self):
        print('milestone_workload_trace_audit:',workload_trace_audit())

    def test_independent_allocation_dp(self):
        rng=random.Random(202609171801);feasible=infeasible=0
        for _ in range(300):
            tasks=[];profile=[]
            for i in range(rng.randint(1,5)):
                c=rng.randint(1,3);d=rng.randint(c,7)
                tasks.append(ap.model.Task(str(i),c,d,rng.randint(d,10),rng.randint(1,7)))
                r=rng.randint(c,d);profile.append((rng.randint(1,r) if c>1 else r,r))
            m,k,length=rng.randint(1,3),rng.randrange(len(tasks)),rng.randint(1,7)
            past=rng.randrange(length)
            u,g,j=rng.randint(0,min(past,tasks[k].wcet-1)),rng.randint(0,length-past-1),rng.randrange(k+1)
            engine=ap.AnalyticEngine(tasks,m,[0]*8,0,profile)
            try:
                upper=engine.bound(k,length,past,u,g,j)
                exact=allocation_dp(tasks,m,profile,k,length,past,u,g,j)
                if upper is None:self.assertIsNone(exact)
                if exact is not None:
                    self.assertIsNotNone(upper);self.assertGreaterEqual(upper,exact);feasible+=1
                else:infeasible+=1
            finally:engine.clear()
        self.assertGreater(feasible,0);self.assertGreater(infeasible,0)
        print('allocation_dp:',dict(cases=300,feasible=feasible,infeasible=infeasible))

    def test_certificates_cuts_scaling_and_tampering(self):
        rows=[([(1,4,5,3),(2,5,7,1),(1,7,9,2)],4), ([(1,6,8,1),(2,6,9,2),(1,7,9,2)],3)]
        for values,quantum in rows:
            ts=[ap.model.Task(str(i),*t) for i,t in enumerate(values)];beta=[quantum*(l//2) for l in range(8)]
            answer=ap.analytic_certificate(ts,2,beta,0)
            self.assertTrue(answer['taskset_proven']);self.assertEqual(answer['certificate']['profile'],[[3,3],[3,5],[7,7]])
            self.assertFalse(ap.analytic_certificate(ts,2,beta,0,first_work=False,first_cuts=False)['taskset_proven'])
            scale=10**6;scaled=[ap.model.Task(t.name,t.wcet,t.deadline,t.period,int(t.power)*scale) for t in ts]
            same=ap.analytic_certificate(scaled,2,[v*scale for v in beta],0)
            self.assertEqual(same['certificate']['profile'],answer['certificate']['profile'])
            self.assertEqual(same['statistics'],answer['statistics'])
            for mode in ('first','full','witness','hash'):
                cert=deepcopy(answer['certificate'])
                if mode=='first':cert['details'][1]['first']['R']=1
                elif mode=='full':cert['profile'][1][1]=4
                elif mode=='witness':cert['details'][1]['full']['groups'].pop()
                else:cert['input_sha256']='changed'
                with self.assertRaises(ValueError):ap.verify_certificate(ts,2,beta,0,cert)
            self.assertEqual(ap.analytic_certificate(ts,2,beta,0,max_iterations=1)['status'],'ITERATION_LIMIT')
        t=[ap.model.Task('startup',1,1,1,1)]
        self.assertFalse(ap.analytic_certificate(t,1,[0,1],0)['taskset_proven'])
        self.assertTrue(ap.analytic_certificate(t,1,[0,1],1)['taskset_proven'])


if __name__=='__main__':unittest.main()
