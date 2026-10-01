"""Configured publication campaigns, batched independent draws and paired variants.

No RTA kernel is modified here. The campaign reuses bounded UUniFast-Discard
and compensated integer WCETs from global_task_generator. Rounding changes
the continuous utilization distribution; we claim neither uniform integer
task sets nor a real-workload distribution. Rejections concern generation
constraints only, never analyzer results. Failed generation fails the batch.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from fractions import Fraction
from itertools import product
import json
import math
import os
from pathlib import Path
import random
import statistics

from .data import ROOT, digest, make_case, save_dataset, load_dataset
from .adapters import METHODS
from .runner import source_identity, run, read_rows
from .report import summarize, write_csv

SCHEMA = 'AB_V7_2_CAMPAIGN_1'
AXES = ('deadline_modes','power_models','priorities','supply_models','capacity_factors','time_factors')

def energy_levels(spec):
    """Fixed coefficients let U_E vary; target U_E rescales coefficients per draw."""
    return spec['ue'] if spec['energy_mode']=='target_ue' else spec['power_scales']

def merge(a,b):
    out = dict(a)
    for key,value in b.items():
        out[key] = merge(out.get(key,{}),value) if isinstance(value,dict) else value
    return out

def settings(config, stage):
    if config.get('schema') != SCHEMA or stage not in config['stages']:
        raise ValueError('unknown campaign schema or stage')
    spec = merge(config['defaults'],config['stages'][stage])
    spec.setdefault('energy_mode','target_ue')
    spec.setdefault('power_scales',['1'])
    if spec['energy_mode'] not in ('target_ue','fixed_scale'):
        raise ValueError('unsupported energy mode')
    for key in ('ue','power_scales'):
        values=spec[key]
        if not values or any(Fraction(v)<=0 for v in values) or len(set(map(Fraction,values)))!=len(values):
            raise ValueError(f'invalid {key}')
    if spec['energy_mode']=='fixed_scale' and len(spec['ue'])!=1:
        raise ValueError('fixed_scale ignores target U_E; use one placeholder ue and power_scales')
    for key in ('processors','task_multipliers','time_factors'):
        if not spec[key] or any(type(v) is not int or v<1 for v in spec[key]) or len(set(spec[key]))!=len(spec[key]):
            raise ValueError(f'invalid {key}')
    for key in AXES:
        values = spec[key]
        if not values or len({digest(v) for v in values})!=len(values):
            raise ValueError(f'empty/duplicate {key}')
    if not spec['uc'] or len(set(map(Fraction,spec['uc'])))!=len(spec['uc']) or any(not 0<Fraction(v)<=1 for v in spec['uc']) or any(Fraction(v)<=0 for v in spec['ue']):
        raise ValueError('invalid utilization grid')
    if any(v not in ('implicit','ratio_0.7_1','uniform_C_T') for v in spec['deadline_modes']):
        raise ValueError('unsupported deadline mode')
    if any(v not in ('workload','homogeneous','heterogeneous_8') for v in spec['power_models']):
        raise ValueError('unsupported power model')
    if any(v not in ('RM','DM') for v in spec['priorities']):
        raise ValueError('unsupported priority')
    if any(v is not None and Fraction(v)<=0 for v in spec['capacity_factors']):
        raise ValueError('invalid capacity factor')
    for supply in spec['supply_models']:
        if supply.get('kind')=='periodic':
            if type(supply.get('gap')) is not int or supply['gap']<1: raise ValueError('invalid gap')
        elif supply.get('kind')=='rate_latency':
            if type(supply.get('latency')) is not int or supply['latency']<0: raise ValueError('invalid latency')
        else: raise ValueError('unknown supply model')
    if not spec['methods'] or len(set(spec['methods']))!=len(spec['methods']) or set(spec['methods'])-set(METHODS):
        raise ValueError('unknown/duplicate methods')
    for key in ('samples','batch_size','repetitions'):
        if type(spec[key]) is not int or spec[key]<1: raise ValueError(f'invalid {key}')
    if not math.isfinite(spec['timeout_seconds']) or spec['timeout_seconds']<=0:
        raise ValueError('invalid timeout')
    gen = spec['generator']
    if not 1<=gen['period_min']<=gen['period_max'] or gen['period_distribution'] not in ('uniform','log_uniform'):
        raise ValueError('invalid period distribution')
    if not 0<=Fraction(gen['min_task_util'])<Fraction(gen['max_task_util'])<=1 or Fraction(gen['tolerance_total'])<0:
        raise ValueError('invalid generator bounds')
    for key in ('rounding_trials','utilization_trials'):
        if type(gen[key]) is not int or gen[key]<1: raise ValueError(f'invalid {key}')
    return spec

def counts(config,stage,count=None):
    s = settings(config,stage)
    draws = len(s['processors'])*len(s['task_multipliers'])*len(s['uc'])*(s['samples'] if count is None else count)
    variants = math.prod(len(s[k]) for k in AXES)*len(energy_levels(s))
    return dict(stage=stage,independent_draws=draws,variants_per_draw=variants,
        inputs=draws*variants,requests=draws*variants*len(s['methods'])*s['repetitions'],
        methods=s['methods'],repetitions=s['repetitions'],timeout_seconds=s['timeout_seconds'])

def skeleton(seed,m,n,uc,gen):
    from global_task_generator import UUniFastDiscard, EnergyAwareTaskGenerator
    from experiments.v9_3.rta_load_cross import FROZEN_WORKLOADS
    # Isolate the legacy sampler's global RNG even on generation exceptions.
    state = random.getstate()
    try:
        random.seed(seed)
        sampler = UUniFastDiscard()
        target = Fraction(uc)*m
        for trial in range(gen['rounding_trials']):
            utils = sampler.generate(n,float(target),min_task_util=float(Fraction(gen['min_task_util'])),
                max_task_util=float(Fraction(gen['max_task_util'])),max_trials=gen['utilization_trials'])
            lo,hi = gen['period_min'],gen['period_max']
            periods = ([random.randint(lo,hi) for _ in range(n)] if gen['period_distribution']=='uniform'
                else [min(hi,max(lo,int(math.exp(random.uniform(math.log(lo),math.log(hi+1)))))) for _ in range(n)])
            c = EnergyAwareTaskGenerator._compensated_execution_times(utils,periods,
                float(Fraction(gen['max_task_util'])),float(target))
            actual = sum(Fraction(x,t) for x,t in zip(c,periods))
            if abs(actual-target)<=Fraction(gen['tolerance_total']):
                tasks = [dict(C=x,T=t,workload=random.choice(FROZEN_WORKLOADS),original_id=i)
                         for i,(x,t) in enumerate(zip(c,periods))]
                return tasks,trial+1
        raise ValueError(f'integer utilization tolerance not reached for seed={seed}, m={m},n={n},uc={uc}')
    finally:
        random.setstate(state)

def generate(config,stage,start,count):
    from experiments.v9_3.rta_load_cross import stable_seed, _load_exact_energy_model
    spec = settings(config,stage)
    if type(start) is not int or type(count) is not int or start<0 or count<1 or start+count>spec['samples']:
        raise ValueError('batch must lie in the predeclared sample range')
    weights = _load_exact_energy_model(ROOT/'system_config_unified_template.yml')
    result = []
    for m,mult,uc in product(spec['processors'],spec['task_multipliers'],spec['uc']):
        n=m*mult
        for index in range(start,start+count):
            seed = stable_seed(config['seed']+spec['seed_offset'],m,n,Fraction(uc),index)
            tasks,trials = skeleton(seed,m,n,uc,spec['generator'])
            cluster = f'{config["seed"]}-{spec["seed_offset"]}-m{m}-n{n}-uc{Fraction(uc)}-i{index}'
            rng = random.Random(seed ^ 0xC0A71A)
            deadlines = dict(implicit=[t['T'] for t in tasks],
                ratio_0_7_1=[max(t['C'],rng.randint(math.ceil(Fraction(7,10)*t['T']),t['T'])) for t in tasks],
                uniform_C_T=[rng.randint(t['C'],t['T']) for t in tasks])
            heterogeneous = [Fraction(rng.choice((1,8))) for _ in tasks]
            cpu = sum(Fraction(t['C'],t['T']) for t in tasks)/m
            for deadline,power,priority,supply,cap_factor,factor,level in product(*(spec[k] for k in AXES),energy_levels(spec)):
                ds = deadlines[deadline.replace('ratio_0.7_1','ratio_0_7_1')]
                ps = ([weights[t['workload']] for t in tasks] if power=='workload' else
                      [Fraction(1)]*n if power=='homogeneous' else heterogeneous)
                demand = sum(Fraction(t['C'],t['T'])*p for t,p in zip(tasks,ps))
                multiplier = (Fraction(level)/demand if spec['energy_mode']=='target_ue' else Fraction(level))
                ps = [p*multiplier for p in ps] # rho=1; fixed_scale never renormalizes to U_E.
                actual_ue = demand*multiplier
                target_ue = str(Fraction(level)) if spec['energy_mode']=='target_ue' else None
                power_scale = str(Fraction(level)) if spec['energy_mode']=='fixed_scale' else None
                order = sorted(range(n),key=lambda i:((tasks[i]['T'] if priority=='RM' else ds[i]),i))
                rows = [(tasks[i]['C']*factor,ds[i]*factor,tasks[i]['T']*factor,ps[i]) for i in order]
                horizon = max(d for _,d,_,_ in rows)
                if supply['kind']=='periodic':
                    gap = supply['gap']*factor
                    beta = [gap*(length//gap) for length in range(horizon+1)]
                else:
                    gap = None
                    latency = supply['latency']*factor
                    beta = [max(0,length-latency) for length in range(horizon+1)]
                reference = sum(sorted(ps,reverse=True)[:m])
                capacity = None if cap_factor is None else Fraction(cap_factor)*reference
                meta = dict(cluster_id=cluster,seed=seed,sample=index,target_uc=str(Fraction(uc)),
                    actual_uc=str(cpu),target_ue=target_ue,actual_ue=str(actual_ue),
                    energy_mode=spec['energy_mode'],power_scale=power_scale,
                    deadline_mode=deadline,power_model=power,priority=priority,original_task_ids=order,
                    workloads=[tasks[i]['workload'] for i in order],time_factor=factor,
                    capacity_factor=cap_factor,capacity_reference='sum_largest_M_unit_powers',
                    supply_model=supply,harvest_gap=gap,harvest_rate='1',
                    period_distribution=spec['generator']['period_distribution'],
                    generator='bounded_UUniFast_Discard_compensated',rounding_trials=trials,
                    stage=stage,mean_energy_overload=actual_ue>1)
                name = cluster+'-'+digest([deadline,power,priority,supply,cap_factor,factor,spec['energy_mode'],level])[:12]
                result.append(make_case(name,rows,m,beta,capacity=capacity,
                    cohort=f'{stage}_{deadline}',metadata=meta))
    if len(result)!=counts(config,stage,count)['inputs']: raise AssertionError('generation count mismatch')
    return result

def prepare(config_path,output,stages,*,start=0,count=None):
    config_path,output = Path(config_path),Path(output)
    config = json.loads(config_path.read_text())
    if output.exists(): raise ValueError('output must be a new directory')
    output.mkdir(parents=True)
    plan = dict(schema=SCHEMA,created_utc=datetime.now(timezone.utc).isoformat(),
        config=config,config_sha256=digest(config),source=source_identity(),
        cli_sha256=digest((ROOT/'scripts/run_ab_v7_2_campaign.py').read_text()),
        generator_dependencies={str(p.relative_to(ROOT)):digest(p.read_text()) for p in
            (ROOT/'global_task_generator.py',ROOT/'experiments/v9_3/rta_load_cross.py',ROOT/'system_config_unified_template.yml')},
        cpu_affinity=[min(os.sched_getaffinity(0))],stages=[],selection='frozen before analyzer results')
    (output/'config.json').write_text(json.dumps(config,indent=2))
    for stage in stages:
        spec = settings(config,stage)
        amount = min(spec['batch_size'],spec['samples']-start) if count is None else count
        cases = generate(config,stage,start,amount)
        generation=dict(config_sha256=plan['config_sha256'],stage=stage,start=start,count=amount,
            spec=spec,generator_dependencies=plan['generator_dependencies'])
        data = save_dataset(output/f'{stage}-dataset.json',cases,generation)
        plan['stages'].append(dict(**counts(config,stage,amount),dataset_sha256=data['dataset_sha256'],
            start=start,count=amount,target_samples_per_cell=spec['samples']))
    # Created only after every requested dataset has been generated and checked.
    (output/'plan.json').write_text(json.dumps(plan,indent=2))
    return plan

def execute(output,*,stages=None,resume=False):
    output = Path(output)
    plan=json.loads((output/'plan.json').read_text())
    if plan['source']!=source_identity() or plan['cli_sha256']!=digest((ROOT/'scripts/run_ab_v7_2_campaign.py').read_text()):
        raise ValueError('campaign code changed after freeze')
    if any(digest((ROOT/p).read_text())!=v for p,v in plan['generator_dependencies'].items()):
        raise ValueError('generator dependencies changed after freeze')
    if digest(plan['config'])!=plan['config_sha256']: raise ValueError('config tampered')
    os.sched_setaffinity(0,set(plan['cpu_affinity']))
    requested = stages or [s['stage'] for s in plan['stages']]
    if set(requested)-{s['stage'] for s in plan['stages']}: raise ValueError('unprepared stage')
    for spec in plan['stages']:
        name=spec['stage']
        if name not in requested: continue
        data = load_dataset(output/f'{name}-dataset.json')
        if data['dataset_sha256']!=spec['dataset_sha256']: raise ValueError('frozen dataset mismatch')
        print(f'START {name}: {spec["requests"]} requests',flush=True)
        log = output/f'{name}.log'
        import contextlib
        with log.open('a' if resume else 'x') as stream, contextlib.redirect_stdout(stream):
            run(output/f'{name}-dataset.json',output/name,tuple(spec['methods']),
                timeout=spec['timeout_seconds'],repetitions=spec['repetitions'],
                resume=resume and (output/name/'manifest.json').exists())
            summary = summarize(output/name)
        if not summary['complete']: raise ValueError('incomplete stage')
        print(f'DONE {name}: {summary["recorded_requests"]} requests',flush=True)

def campaign_summary(output, inputs=None):
    """Report each cell. Timeouts remain unresolved; only completed pairs compare R.

    Certification never pools different stages or repeats. Repeated time medians
    require all repetitions to finish; censored inputs are reported explicitly.
    """
    output = Path(output)
    roots = [Path(p) for p in inputs] if inputs else [output]
    plans = [json.loads((root/'plan.json').read_text()) for root in roots]
    plan = plans[0]
    if any(p['config_sha256']!=plan['config_sha256'] or p['source']!=plan['source'] for p in plans):
        raise ValueError('cannot combine different configs or code')
    combined={}
    provenance=[]
    for root,other in zip(roots,plans):
        for spec in other['stages']:
            name=spec['stage']; directory=root/name
            item=combined.setdefault(name,dict(spec=spec,cases={},rows=[],expected=0,environment=None))
            item['expected']+=spec['requests']
            if not (directory/'manifest.json').exists(): continue
            summary=summarize(directory)
            manifest=json.loads((directory/'manifest.json').read_text())
            if item['environment'] is not None and item['environment']!=manifest['environment']:
                raise ValueError('do not pool timings from different environments')
            item['environment']=manifest['environment']
            cases={c['case_id']:c for c in load_dataset(directory/'dataset.json')['cases']}
            if set(cases)&set(item['cases']): raise ValueError('overlapping input batches')
            item['cases'].update(cases);item['rows'].extend(read_rows(directory/'results.jsonl'))
            provenance.append(dict(output=str(root.resolve()),stage=name,manifest=manifest,
                                   complete=summary['complete']))
    if inputs:
        output.mkdir(parents=True,exist_ok=False)
        (output/'combined-provenance.json').write_text(json.dumps(provenance,indent=2))
    stages=[];cells=[];pairs=[]
    for name,item in combined.items():
        spec=item['spec'];cases=item['cases'];rows=item['rows'];expected=item['expected']
        if not cases:
            stages.append(dict(stage=name,expected=expected,recorded=0,complete=False))
            continue
        stages.append(dict(stage=name,expected=expected,recorded=len(rows),complete=len(rows)==expected,
            statuses=dict(Counter(r['status'] for r in rows)),
            total_request_wall_seconds=sum(r.get('request_wall_seconds',0) for r in rows)))
        grouped=defaultdict(list)
        keys=('target_uc','target_ue','energy_mode','power_scale','deadline_mode','power_model','priority','supply_model',
              'capacity_factor','time_factor','period_distribution')
        for cid,c in cases.items():
            meta=c['metadata'];label=dict(stage=name,processors=c['model']['processors'],tasks=len(c['model']['tasks']),
                                        **{k:meta.get(k, 'target_ue' if k=='energy_mode' else None) for k in keys})
            grouped[json.dumps(label,sort_keys=True)].append(cid)
        for label,ids in grouped.items():
            context=json.loads(label)
            observed=[Fraction(cases[cid]['metadata']['actual_ue']) for cid in ids]
            context.update(observed_ue_min=str(min(observed)),observed_ue_max=str(max(observed)),
                           observed_ue_median=str(statistics.median(observed)))
            id_set=set(ids)
            by={(r['case_id'],r['config']['method'],r['repetition']):r for r in rows if r['case_id'] in id_set}
            # The CI is for certificates found under the fixed budget, not for
            # latent schedulability. Unresolved pairs are still counted below.
            boot=point=None
            clusters=defaultdict(list)
            for cid in ids: clusters[cases[cid]['metadata']['cluster_id']].append(cid)
            if len(clusters)>=30 and all((cid,m,0) in by for cid in ids for m in spec['methods']):
                import numpy as np
                values=np.asarray([[statistics.mean(by[cid,m,0]['status']=='CERTIFIED' for cid in group)
                    for m in spec['methods']] for _,group in sorted(clusters.items())],dtype=float)
                seed=plan['config'].get('statistics',{}).get('bootstrap_seed',20261006)^int(digest(label)[:16],16)
                rng=np.random.default_rng(seed);n=len(values)
                resamples=plan['config'].get('statistics',{}).get('bootstrap_resamples',5000)
                boot=rng.multinomial(n,np.full(n,1/n),size=resamples)@values/n
                point=values.mean(axis=0)
            for method in spec['methods']:
                primary=[by[cid,method,0] for cid in ids if (cid,method,0) in by]
                counted=Counter(r['status'] for r in primary)
                times=[];requests=[];unfinished=0
                for cid in ids:
                    repeat=[by[cid,method,i] for i in range(spec['repetitions']) if (cid,method,i) in by]
                    if len(repeat)==spec['repetitions'] and all(r['status'] in ('CERTIFIED','NO_CERTIFICATE') for r in repeat):
                        times.append(statistics.median(r['analysis_wall_seconds'] for r in repeat))
                        requests.append(statistics.median(r['request_wall_seconds'] for r in repeat))
                    else: unfinished+=1
                ci=(np.quantile(boot[:,spec['methods'].index(method)],[.025,.975]).tolist() if boot is not None else None)
                cells.append(dict(**context,method=method,independent_draws=len(clusters),
                    primary_inputs=len(primary),statuses=dict(counted),
                    certified_fraction_under_budget=counted['CERTIFIED']/len(primary) if primary else None,
                    certified_fraction_under_budget_ci95=ci,
                    inputs_with_all_repetitions_completed=len(times),censored_or_missing_inputs=unfinished,
                    median_completed_case_analysis_seconds=statistics.median(times) if times else None,
                    median_completed_case_request_seconds=statistics.median(requests) if requests else None))
            for a,b in product(spec['methods'],spec['methods']):
                if spec['methods'].index(a)>=spec['methods'].index(b):continue
                matched=[(by[cid,a,0],by[cid,b,0]) for cid in ids if (cid,a,0) in by and (cid,b,0) in by]
                completed=[(x,y) for x,y in matched if x['status'] in ('CERTIFIED','NO_CERTIFICATE') and y['status'] in ('CERTIFIED','NO_CERTIFICATE')]
                common=[(x,y) for x,y in completed if x['status']==y['status']=='CERTIFIED']
                ia,ib=spec['methods'].index(a),spec['methods'].index(b)
                pairs.append(dict(**context,a=a,b=b,matched=len(matched),unresolved=len(matched)-len(completed),
                    paired_certification_difference_under_budget_b_minus_a=float(point[ib]-point[ia]) if point is not None else None,
                    paired_certification_difference_under_budget_ci95=np.quantile(boot[:,ib]-boot[:,ia],[.025,.975]).tolist() if boot is not None else None,
                    both_certified=len(common),a_only=sum(x['status']=='CERTIFIED' and y['status']=='NO_CERTIFICATE' for x,y in completed),
                    b_only=sum(y['status']=='CERTIFIED' and x['status']=='NO_CERTIFICATE' for x,y in completed),
                    b_tighter_R=sum(x['response_bounds']!=y['response_bounds'] and all(p>=q for p,q in zip(x['response_bounds'],y['response_bounds'])) for x,y in common)))
    result=dict(stages=stages,cells=cells,pairs=pairs,statistics=plan['config'].get('statistics'),
        interpretation='NO_CERTIFICATE is not infeasibility. TIMEOUT is censored, not a failed certificate. '
                       'Qualification cells have two draws and are not formal publication estimates.')
    (output/'campaign-summary.json').write_text(json.dumps(result,indent=2))
    write_csv(output/'campaign-cells.csv',cells);write_csv(output/'campaign-pairs.csv',pairs)
    return result
