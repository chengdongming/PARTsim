"""Counterfactual predicates on the same proven full profile; not timing."""
from pathlib import Path
from collections import Counter
import json,os,resource,sys,traceback
ROOT=Path(__file__).resolve().parents[1]

def diagnose(case,certificate):
    import unified_kernel as kernel
    sr=kernel.sr;tasks,beta,m,e0,opts=kernel.model_inputs(case)
    profile=certificate['profile']; counters=Counter();examples={}

    class TraceEngine(kernel.UnifiedEngine):
        def __init__(self,*args,**kwargs):
            self.aux={}
            super().__init__(*args,**kwargs)
        def example(self,kind,item):
            arr=examples.setdefault(kind,[])
            if len(arr)<4:arr.append(item)
        def _work(self,i,length):
            value=super()._work(i,length)
            if 'no_first_work' in self.aux:
                off=self.aux['no_first_work'].work(i,length)
                assert 0<=off-value<=1
                counters['unique_work_queries']+=1
                if value<off:
                    counters['work_queries_first_item_reduced']+=1
                    self.example('work',{'task_index':i,'length':length,'full':value,'without_first_work':off})
            return value
        def _analytic_context(self,k,length,past,g,blocker):
            value=super()._analytic_context(k,length,past,g,blocker)
            if value is not None and 'no_shared_budget' in self.aux:
                independent=self.aux['no_shared_budget'].analytic_context(k,length,past,g,blocker)
                assert independent>=value
                counters['feasible_analytic_contexts']+=1
                if independent>value:
                    counters['contexts_positive_suffix_reservation_loss']+=1
                    self.example('suffix_budget',{'k':k,'L':length,'past':past,'g':g,'blocker':blocker,
                        'prefix_without_sharing':independent,'prefix_with_sharing':value,'loss':independent-value})
            return value
        def _bound(self,*args):
            value=super()._bound(*args)
            if value is not None and self.aux:
                for mode in ['no_shared_budget','no_first_work']:
                    off=self.aux[mode].bound(*args)
                    assert off is not None and off>=value
                    counters['feasible_bound_queries_'+mode]+=1
                    if off>value:counters['strict_bound_queries_'+mode]+=1
            return value
        def _account(self,k,q,w,b,z,blocker,past,all_blockers):
            value=super()._account(k,q,w,b,z,blocker,past,all_blockers)
            if self.aux:
                supply=self.e0+self.beta[b] if past==b else self.beta[past+1]
                for mode in ['no_first_cuts','no_first_work','no_shared_budget']:
                    off=self.aux[mode].account(k,q,w,b,z,blocker,past,all_blockers)
                    counters['account_queries_'+mode]+=1
                    if value is not None:
                        assert off is not None and off>=value
                    changed=(value is None and off is not None) or (value is not None and off>value)
                    if changed:counters['changed_accounts_'+mode]+=1
                    crossed=(value is None or value<=supply) and off is not None and off>supply
                    if crossed:
                        counters['supply_crossing_accounts_'+mode]+=1
                        self.example(mode,{'k':k,'q':q,'w':w,'b':b,'z':z,'blocker':blocker,
                            'past':past,'all_blockers':all_blockers,'full_upper':value,
                            'without_constraint_upper':off,'supply':supply})
            return value

    engine=TraceEngine(tasks,m,beta,e0,profile,mode='full',**opts)
    aux={mode:kernel.UnifiedEngine(tasks,m,beta,e0,profile,mode=mode,**opts)
         for mode in ['no_first_cuts','no_first_work','no_shared_budget','completion_only']}
    engine.aux=aux
    task_checks=[]
    try:
        for k,(task,(f,r)) in enumerate(zip(tasks,profile)):
            detail=engine.window(k,task.wcet,r); assert detail is not None
            off={mode:aux[mode].window(k,task.wcet,r) for mode in aux}
            task_checks.append({'k':k,'F':f,'R':r,'C':task.wcet,'full_kind':detail['kind'],
                'same_X_completion_window_pass':{mode:value is not None for mode,value in off.items()},
                'first_history_z0_branch_positions_excluded':max(0,r-f) if task.wcet>1 else 0})
    finally:
        engine.clear()
        for v in aux.values():v.clear()
    return {'status':'PASS','profile':profile,'task_checks':task_checks,
        'counters':dict(counters),'examples':examples,
        'interpretation':'Unique cached queries during full final-R checks; counterfactual predicates use identical X. Query counts and local threshold crossings are not population certification gains.'}

def main(payload):
    os.sched_setaffinity(0,{payload.get('host_cpu',0)})
    limit=payload.get('memory_limit_mib',1024)*1024*1024
    resource.setrlimit(resource.RLIMIT_AS,(limit,limit))
    if payload['job']=='affine_completion':
        import affine_completion
        return affine_completion.analyze(payload['case'])
    if payload['job']=='diagnose':return diagnose(payload['case'],payload['certificate'])
    if payload['job']=='co_embed':
        import kernel_worker
        import hashlib
        case=payload['case'];old=payload['certificate'];tasks=case['model']['tasks']
        assert old['mode']=='completion_only' and old['input_sha256']==case['input_sha256']
        assert all(f==r for f,r in old['profile'])
        profile=[[r-t['C']+1,r] for t,(f,r) in zip(tasks,old['profile'])]
        engine=kernel_worker.setup_engine(case,profile,'full',True)
        details=[];implied=[]
        try:
            for k,(t,(f,r)) in enumerate(zip(tasks,profile)):
                full=engine.window(k,t['C'],r)
                assert full is not None,'derived profile failed original full window'
                use_completion=t['C']==1
                first=({'kind':'FULL_COMPLETION_IMPLIES_FIRST','R':r} if use_completion
                       else engine.window(k,1,f))
                assert first is not None,'shifted first window failed original predicate'
                implied.append(use_completion);details.append({'first':first,'full':full})
        finally:engine.clear()
        evidence=hashlib.sha256(json.dumps(details,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        cert={'schema':'AB_UNIFIED_ABLATION_POST_FIXED_POINT_1','mode':'full',
            'input_sha256':case['input_sha256'],'profile':profile,
            'first_implied_by_completion':implied,'evidence_sha256':evidence}
        return {'status':'PASS','derived_profile':profile,'derived_certificate':cert,
                'interpretation':'Original full predicates replayed at F=R-C+1; post-fixed certificate, not a new least-bound solve.'}
    if payload['job']=='derived_compact_verify':
        import unified_kernel
        return unified_kernel.verify_compact(payload['case'],payload['certificate'])
    import research_worker
    return research_worker.main(payload)

if __name__=='__main__':
    try: answer=main(json.load(sys.stdin))
    except Exception: answer={'status':'ERROR','traceback':traceback.format_exc()}
    Path(sys.argv[1]).write_text(json.dumps(answer,ensure_ascii=False,allow_nan=False))
