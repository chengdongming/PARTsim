"""Original campaign generation functions, with standalone imports.

Generated task models and case IDs are checked against the frozen archives.
The RTA and SEQ kernels are separate byte-for-byte source snapshots.
"""
from fractions import Fraction
from itertools import product
import math
import random
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent / 'reproduction/optimized'
sys.path.insert(0, str(ROOT))
from experiments.ab_v7_2.data import digest, make_case
SCHEMA = 'AB_V7_2_CAMPAIGN_1'
AXES = ('deadline_modes','power_models','priorities','supply_models','capacity_factors','time_factors')
METHODS = ('seq', 'v72_analytic', 'v72_flow', 'ablation_full', 'ablation_completion_only',
           'ablation_no_first_work', 'ablation_no_first_cuts', 'ablation_no_shared_budget',
           'ablation_progress_only', 'ablation_last_block_only')

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
    from generator_support import FROZEN_WORKLOADS
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
    from generator_support import stable_seed, _load_exact_energy_model
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
