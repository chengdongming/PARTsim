"""Five ablations sharing the frozen optimized analytic evaluation kernel.

The sufficient predicates and bottom-iteration search are unchanged. Only
the named mechanism switches differ. No source in reproduction/ is edited.
"""
from pathlib import Path
from collections import Counter
import hashlib
import json
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
CODE = ROOT / 'reproduction' / 'optimized'
AB = CODE / 'research/rta/an_ab_joint/ab'
sys.path.insert(0, str(CODE))
sys.path.insert(0, str(AB))
import ab_structural_rta as sr

MODES = ('full', 'no_first_cuts', 'no_first_work', 'no_shared_budget', 'completion_only')


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class UnifiedEngine(sr.StructuralEngine):
    def __init__(self, *args, mode='full', **kwargs):
        if mode not in MODES:
            raise ValueError(mode)
        self.mode = mode
        self.operation_counts = Counter()
        super().__init__(*args, **kwargs)
        self.first_work = mode not in ('no_first_work','completion_only')
        self.first_cuts = mode not in ('no_first_cuts','completion_only')
        self.shared_budget = mode != 'no_shared_budget'

    def _analytic_context(self, k, length, past, g, blocker):
        self.operation_counts['context_evaluations'] += 1
        tail=length-past-1
        hp=[]
        for i in range(k):
            whole,history=self.work(i,length),self.work(i,past)
            current=int(i<blocker)
            cap=min(whole,self.work(i,past+1)-int(i==blocker),history+current,past+current)
            if cap<0:
                return None
            tail_cap=min(g,self.work(i,tail))
            hp.append((cap,tail_cap,min(tail_cap,whole-cap)))
        if sum(v[1] for v in hp)<self.m*g:
            return None
        prefix=sum(self.powers[i]*v[0] for i,v in enumerate(hp))
        if not self.shared_budget:
            return prefix
        needed=max(0,self.m*g-sum(v[2] for v in hp))
        loss=0
        for i in self.hp_ascending[k]:
            cap,tail_cap,free=hp[i]
            take=min(needed,tail_cap-free)
            loss+=take*self.powers[i]
            needed-=take
        sr.model.require(needed==0,'inconsistent tail relaxation')
        return prefix-loss

    def _bound(self, *args):
        self.operation_counts['bound_evaluations'] += 1
        return super()._bound(*args)

    def _account(self,k,q,w,b,z,blocker,past,all_blockers):
        self.operation_counts['account_evaluations'] += 1
        first=self.profile[k][0] if q>1 and self.first_cuts else w+1
        lo=max(0,z-(b-past))
        hi=min(z,past,z-int(b-past>=first))
        if lo>hi:
            return None
        g=max(0,w-b-1-(q-1-z))
        extra=max(self.powers[:k+1])-self.powers[k] if all_blockers else 0

        def at(u):
            self.operation_counts['integer_bound_queries'] += 1
            return self.bound(k,w-b+past,past,u,g,blocker)

        value=at(lo)
        if value is None:
            return None
        if hi-lo<8:
            self.operation_counts['small_integer_ranges'] += 1
            maximum=value
            for u in range(lo+1,hi+1):
                maximum=max(maximum,at(u))
        else:
            self.operation_counts['binary_integer_ranges'] += 1
            left,right=lo,hi
            while left<right:
                mid=(left+right)//2
                if at(mid)<at(mid+1):
                    left=mid+1
                else:
                    right=mid
            maximum=at(left)
        return min(maximum+extra,(past+1)*self.slot_energy_cap)

    def window(self,*args):
        self.operation_counts['window_calls'] += 1
        return super().window(*args)


def model_inputs(case):
    m=case['model']
    sr.model.require(m['capacity'] is None and m['background_power']==0,
                     'this campaign is unlimited-capacity and zero-background')
    tasks=[sr.model.Task(t['name'],t['C'],t['D'],t['T'],t['power']) for t in m['tasks']]
    tasks,beta=sr.model.normalize(tasks,m['processors'],m['beta'],m['release_floor'])
    opts={'capacity':None,'background_power':0,'bound':'analytic','legacy_v4':False}
    return tasks,beta,m['processors'],m['release_floor'],opts


def verify_compact(case,certificate,engine_type=UnifiedEngine):
    tasks,beta,m,e0,opts=model_inputs(case)
    sr.model.require(certificate['input_sha256']==case['input_sha256'],'input mismatch')
    mode=certificate['mode']
    sr.model.require(mode in MODES,'unknown mode')
    profile=tuple(tuple(p) for p in certificate['profile'])
    sr.model.require(len(profile)==len(tasks),'incomplete profile')
    sr.model.require(len(certificate['first_implied_by_completion'])==len(tasks),
                     'incomplete first-unit implication vector')
    engine=engine_type(tasks,m,beta,e0,profile,mode=mode,**opts)
    details=[]
    try:
        for k,(t,(f,r),implied) in enumerate(zip(tasks,profile,certificate['first_implied_by_completion'])):
            sr.model.require(1<=f<=r<=t.deadline and t.wcet<=r,'invalid bound')
            full=engine.window(k,t.wcet,r)
            sr.model.require(full is not None,'full post-fixed-point predicate failed')
            if implied:
                sr.model.require(f==r,'invalid completion-to-first implication')
                first={'kind':'FULL_COMPLETION_IMPLIES_FIRST','R':r}
            else:
                first=engine.window(k,1,f)
                sr.model.require(first is not None,'first post-fixed-point predicate failed')
            details.append({'first':first,'full':full})
        sr.model.require(digest(details)==certificate['evidence_sha256'],'rebuilt evidence differs')
    finally:
        engine.clear()
    return {'status':'PASS','tasks':len(tasks),'mode':mode,'evidence_sha256':certificate['evidence_sha256']}


def analyze(case,mode,verify=True):
    if mode not in MODES:
        raise ValueError(mode)
    tasks,beta,m,e0,opts=model_inputs(case)
    wall,cpu=time.perf_counter(),time.process_time()
    profile=tuple((t.wcet if mode=='completion_only' else 1,t.wcet) for t in tasks)
    natural=1+sum(t.deadline-t.wcet+(t.deadline-1 if t.wcet>1 else 0) for t in tasks)
    history=[]
    counts=Counter()
    phase_cpu={'first':0.0,'completion':0.0}
    details=[]
    for iteration in range(natural):
        engine=UnifiedEngine(tasks,m,beta,e0,profile,mode=mode,**opts)
        output,details=[],[]
        iteration_counts={}
        try:
            for k,t in enumerate(tasks):
                started=time.process_time()
                full=engine.full_candidate(k)
                phase_cpu['completion']+=time.process_time()-started
                if full is None:
                    break
                started=time.process_time()
                first=(engine.candidate(k,1,1,full['R']-1)
                       if t.wcet>1 and mode!='completion_only' else None)
                phase_cpu['first']+=time.process_time()-started
                if first is None:
                    first={'kind':'FULL_COMPLETION_IMPLIES_FIRST','R':full['R']}
                output.append((first['R'],full['R']))
                details.append({'first':first,'full':full})
            iteration_counts=dict(engine.operation_counts)
            counts.update(engine.operation_counts)
            counts.update({'account_cache_hits':engine.account.cache_info().hits,
                           'bound_cache_hits':engine.bound.cache_info().hits})
        finally:
            engine.clear()
        history.append({'input':profile,'output':output,'counts':iteration_counts})
        if len(output)!=len(tasks):
            status='NO_CERTIFICATE'
            certificate=None
            break
        sr.model.require(all(f<=a and r<=b for (f,r),(a,b) in zip(profile,output)),
                         'bottom iteration monotonicity violated')
        if tuple(output)==profile:
            status='CERTIFIED'
            certificate={'schema':'AB_UNIFIED_ABLATION_POST_FIXED_POINT_1','mode':mode,
                'input_sha256':case['input_sha256'],'profile':output,
                'first_implied_by_completion':[d['first']['kind']=='FULL_COMPLETION_IMPLIES_FIRST' for d in details],
                'evidence_sha256':digest(details)}
            break
        profile=tuple(output)
    else:
        raise AssertionError('finite lattice iteration did not stabilize')
    solve_wall=time.perf_counter()-wall
    solve_cpu=time.process_time()-cpu
    verification=None
    vw,vc=time.perf_counter(),time.process_time()
    if certificate is not None and verify:
        verification=verify_compact(case,certificate)
    verify_wall=time.perf_counter()-vw if verification else 0.0
    verify_cpu=time.process_time()-vc if verification else 0.0
    return {'status':status,'first_bounds':[p[0] for p in certificate['profile']] if certificate else None,
            'response_bounds':[p[1] for p in certificate['profile']] if certificate else None,
            'certificate':certificate,'iterations':iteration+1,'operation_counts':dict(counts),
            'phase_cpu_seconds':phase_cpu,'solve_wall_seconds':solve_wall,'solve_cpu_seconds':solve_cpu,
            'verification_wall_seconds':verify_wall,'verification_cpu_seconds':verify_cpu,
            'analysis_wall_seconds':time.perf_counter()-wall,'analysis_cpu_seconds':time.process_time()-cpu,
            'verification_status':'PASS' if verification else None,'history':history}
