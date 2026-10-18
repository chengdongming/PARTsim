"""Fresh-process entry for the shared kernel and frozen reference checks."""
from pathlib import Path
import json,os,sys,time,traceback

ROOT=Path(__file__).resolve().parent.parent


def reference_import():
    repo=ROOT/'reproduction'/'original_verifier'
    sys.path.insert(0,str(repo))
    sys.path.insert(0,str(repo/'research/rta/an_ab_joint/ab'))
    import claim_audit_v7_2 as audit
    return audit


def setup_engine(case,profile,mode,reference):
    if reference:
        audit=reference_import();sr=audit.sr;cls=audit.AuditEngine
    else:
        import unified_kernel as kernel
        sr=kernel.sr;cls=kernel.UnifiedEngine
    model=case['model']
    tasks=[sr.model.Task(t['name'],t['C'],t['D'],t['T'],t['power']) for t in model['tasks']]
    return cls(tasks,model['processors'],model['beta'],model['release_floor'],profile,
        mode=mode,capacity=None,background_power=0,bound='analytic',legacy_v4=False)


def primitive_batch(payload):
    reference=payload['reference']
    cases=payload['cases'];contexts=payload['contexts'];answer=[]
    binary=0
    for mode in payload['modes']:
        for c in contexts:
            engine=setup_engine(cases[c['case']],c['profile'],mode,reference)
            try:
                k,q,w,b,z,blocker,past,all_blockers=(c[n] for n in ['k','q','w','b','z','blocker','past','all_blockers'])
                first=engine.profile[k][0] if q>1 and engine.first_cuts else w+1
                lo=max(0,z-(b-past));hi=min(z,past,z-int(b-past>=first))
                value=engine.account(k,q,w,b,z,blocker,past,all_blockers)
                g=max(0,w-b-1-(q-1-z))
                values=[engine.bound(k,w-b+past,past,u,g,blocker) for u in range(lo,hi+1)]
                if values and values[0] is not None:
                    assert all(v is not None for v in values),'u-dependent feasibility'
                    slopes=[b-a for a,b in zip(values,values[1:])]
                    assert all(a>=b for a,b in zip(slopes,slopes[1:])),'discrete concavity violated'
                if not reference:
                    binary+=engine.operation_counts['binary_integer_ranges']
                answer.append({'mode':mode,'value':value,'values':values,'lo':lo,'hi':hi})
            finally:engine.clear()
    return {'evaluations':answer,'binary_ranges':binary}


def bounded_batch(payload):
    answer=[]
    if payload['reference']:
        audit=reference_import()
    else:
        import unified_kernel as kernel
    for i,case in enumerate(payload['cases']):
        for mode in payload['modes']:
            if payload['reference']:
                m=case['model'];rows=[[t[k] for k in ['C','D','T','power']] for t in m['tasks']]
                r=audit.audit_run(rows,m['processors'],m['beta'],None,mode,
                    e0=m['release_floor'],check_production=False,return_details=False)
                result={'status':'CERTIFIED' if r['profile'] else 'NO_CERTIFICATE',
                        'profile':r['profile'],'iterations':r['iterations']}
            else:
                r=kernel.analyze(case,mode,verify=True)
                result={'status':r['status'],'profile':r['certificate']['profile'] if r['certificate'] else None,
                        'iterations':r['iterations']}
                if mode=='full':
                    tasks,beta,m,e0,opts=kernel.model_inputs(case)
                    production=kernel.sr.least_certificate(tasks,m,beta,e0,**opts,verify=True)
                    assert production['proven']==(r['status']=='CERTIFIED'),'production status mismatch'
                    if production['proven']:
                        assert production['certificate']['profile']==[list(p) for p in r['certificate']['profile']]
            answer.append({'case':i,'mode':mode,**result})
    return {'evaluations':answer}


def reference_verify(payload):
    audit=reference_import();case=payload['case'];cert=payload['certificate'];m=case['model']
    import hashlib
    def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    assert cert['input_sha256']==case['input_sha256']
    assert len(cert['profile'])==len(cert['first_implied_by_completion'])==len(m['tasks'])
    engine=setup_engine(case,cert['profile'],cert['mode'],True)
    details=[]
    started=time.perf_counter()
    try:
        for k,(t,(f,r),implied) in enumerate(zip(m['tasks'],cert['profile'],cert['first_implied_by_completion'])):
            assert 1<=f<=r<=t['D'] and t['C']<=r
            full=engine.window(k,t['C'],r)
            assert full is not None,'reference full predicate failed'
            if implied:
                assert f==r
                first={'kind':'FULL_COMPLETION_IMPLIES_FIRST','R':r}
            else:
                first=engine.window(k,1,f)
                assert first is not None,'reference first predicate failed'
            details.append({'first':first,'full':full})
        assert digest(details)==cert['evidence_sha256'],'reference evidence mismatch'
    finally:engine.clear()
    return {'status':'PASS','seconds':time.perf_counter()-started,'evidence_sha256':cert['evidence_sha256']}


def main(payload):
    if 'host_cpu' in payload:
        os.sched_setaffinity(0,{payload['host_cpu']})
    if payload.get('memory_limit_mib'):
        import resource
        limit=payload['memory_limit_mib']*1024*1024
        resource.setrlimit(resource.RLIMIT_AS,(limit,limit))
    job=payload['job']
    if job=='primitive':return primitive_batch(payload)
    if job=='bounded':return bounded_batch(payload)
    if job=='reference_verify':return reference_verify(payload)
    import unified_kernel as kernel
    from experiments.ab_v7_2.data import validate_case
    validate_case(payload['case'])
    return kernel.analyze(payload['case'],payload['mode'],verify=True)


if __name__=='__main__':
    try:
        result=main(json.load(sys.stdin))
    except Exception:
        result={'status':'ERROR','traceback':traceback.format_exc()}
    try:
        import resource
        result['peak_rss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
    except ImportError:pass
    text=json.dumps(result,ensure_ascii=False)
    if len(sys.argv)>1:Path(sys.argv[1]).write_text(text)
    else:print(text)
