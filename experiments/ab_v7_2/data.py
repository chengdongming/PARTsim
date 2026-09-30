"""Frozen, exact inputs shared by SEQ, production V7.2 and its ablations.

Task list order IS fixed priority (highest first). beta is a raw-harvest lower
bound in any window; debit precedes harvest. release_floor applies at EVERY
release. None capacity means no overflow. All energy quantities are scaled by
one exact common denominator before ANY method runs; there is no rounding.
"""
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 'AB_V7_2_DATASET_1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def exact(value):
    if type(value) not in (str, int, Fraction):
        raise ValueError('energy must be an integer or exact rational string')
    return Fraction(value)


def integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f'expected integer >= {minimum}: {value!r}')
    return value


def make_case(name, rows, m, beta, *, capacity=None, release_floor=0,
              background_power=0, cohort='generated', metadata=None):
    """rows are [C,D,T,power], in the SAME priority order for all methods."""
    integer(m, 1)
    if not rows or any(len(r) != 4 for r in rows):
        raise ValueError('nonempty C,D,T,power rows required')
    powers = [exact(r[3]) for r in rows]
    supply = [exact(v) for v in beta]
    floor, bg = exact(release_floor), exact(background_power)
    cap = None if capacity is None else exact(capacity)
    for c, d, t, _p in rows:
        if not 1 <= integer(c, 1) <= integer(d, 1) <= integer(t, 1):
            raise ValueError('require C <= D <= T')
    if any(p <= 0 for p in powers) or floor < 0 or bg < 0:
        raise ValueError('invalid power or release floor')
    if cap is not None and (cap <= 0 or floor > cap):
        raise ValueError('capacity must be positive and >= release floor')
    if (len(supply) <= max(r[1] for r in rows) or supply[0] != 0
            or any(a > b for a, b in zip(supply, supply[1:]))):
        raise ValueError('beta must start at zero, be monotone and cover max(D)')
    energies = powers + supply + [floor, bg] + ([] if cap is None else [cap])
    scale = math.lcm(*(v.denominator for v in energies))
    scaled = lambda v: int(v * scale)
    model = dict(tasks=[dict(name=str(i+1), C=r[0], D=r[1], T=r[2],
                            power=scaled(p)) for i, (r, p) in enumerate(zip(rows, powers))],
                 processors=m, beta=list(map(scaled, supply)), release_floor=scaled(floor),
                 background_power=scaled(bg), capacity=None if cap is None else scaled(cap),
                 energy_scale=scale, priority='INPUT_ORDER', harvest_order='DEBIT_BEFORE_HARVEST')
    case = dict(name=name, cohort=cohort, model=model, metadata=metadata or {})
    case['input_sha256'] = digest(model)
    case['case_id'] = digest(case)
    return case


def validate_case(case):
    model = case['model']
    if model['priority'] != 'INPUT_ORDER' or model['harvest_order'] != 'DEBIT_BEFORE_HARVEST':
        raise ValueError('unsupported scheduling semantics')
    integer(model['energy_scale'], 1)
    if [t['name'] for t in model['tasks']] != [str(i+1) for i in range(len(model['tasks']))]:
        raise ValueError('task names must follow the saved priority order')
    # Validate persisted integers without silently repairing malformed inputs.
    for value in model['beta'] + [model['release_floor'], model['background_power']]:
        integer(value)
    for task in model['tasks']:
        integer(task['power'], 1)
    if model['capacity'] is not None:
        integer(model['capacity'], 1)
    make_case(case['name'], [[t[k] for k in ('C','D','T','power')] for t in model['tasks']],
              model['processors'], model['beta'], capacity=model['capacity'],
              release_floor=model['release_floor'], background_power=model['background_power'])
    if case['input_sha256'] != digest(model):
        raise ValueError('input hash mismatch')
    if case['case_id'] != digest({k:v for k,v in case.items() if k != 'case_id'}):
        raise ValueError('case hash mismatch')


def save_dataset(path, cases, generation):
    for case in cases:
        validate_case(case)
    if not cases or len({c['case_id'] for c in cases}) != len(cases):
        raise ValueError('empty dataset or duplicate case IDs')
    data = dict(schema=SCHEMA, generation=generation, cases=cases)
    data['dataset_sha256'] = digest(data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(data, f, indent=2, allow_nan=False)
    return data


def load_dataset(path):
    data = json.loads(Path(path).read_text())
    if data['schema'] != SCHEMA or data['dataset_sha256'] != digest(
            {k:v for k,v in data.items() if k != 'dataset_sha256'}):
        raise ValueError('dataset schema/hash mismatch')
    if not data['cases'] or len({c['case_id'] for c in data['cases']}) != len(data['cases']):
        raise ValueError('empty dataset or duplicate case IDs')
    for case in data['cases']:
        validate_case(case)
    return data


def mechanism_cases():
    return [
        make_case('frozen_holdout_025', [(1,4,10,2),(2,5,9,3),(1,6,7,1)],
                  1, [2*l for l in range(7)], cohort='mechanism'),
        make_case('joint_first_history', [(1,4,5,3),(2,5,7,1),(1,7,9,2)],
                  2, [4*(l//2) for l in range(8)], capacity=8, cohort='mechanism'),
        make_case('zero_slot_progress', [(1,5,9,1)]*2,
                  2, [2*(l//2) for l in range(6)], capacity=1, cohort='mechanism'),
        make_case('critical_background', [(1,3,3,1),(2,4,5,1)],
                  2, [3*l for l in range(5)], capacity=3, background_power=3,
                  cohort='mechanism')]


def generate_cases(config):
    """Reuse the reviewed legacy CPU generator; never filter by RTA outcome.

    Supply is quantum * floor(L / harvest_gap). Powers are workload ratios
    selected from the old config, or homogeneous ones. U_E scales powers
    exactly against mean supply, preserving the CPU skeleton across U_E.
    """
    from experiments.v9_3.rta_load_cross import (
        generate_cpu_skeleton, stable_seed, _load_exact_energy_model)
    system = Path(config['system_config'])
    base = _load_exact_energy_model(system)
    cases = []
    for m in config['processors']:
        for n in config['tasks']:
            for uc_text in config['uc']:
                uc = exact(uc_text)
                if not 0 < uc <= 1:
                    raise ValueError('U_C must be in (0,1]')
                for index in range(config['samples']):
                    seed = stable_seed(config['seed'], m, n, uc, index)
                    state = random.getstate()
                    try:
                        skeleton = generate_cpu_skeleton(
                            seed=seed, target_uc=uc, processors=m, tasks=n,
                            period_min=config['period_min'], period_max=config['period_max'],
                            min_task_util=exact(config['min_task_util']),
                            max_task_util=exact(config['max_task_util']),
                            tolerance_total=exact(config['tolerance']), system_config=system)
                    finally:
                        random.setstate(state)
                    weights = [Fraction(1) if config['power_model'] == 'homogeneous'
                               else base[t['workload']] for t in skeleton]
                    demand = sum(Fraction(t['C'],t['T'])*p for t,p in zip(skeleton,weights))
                    for ue_text in config['ue']:
                        ue = exact(ue_text)
                        if ue <= 0:
                            raise ValueError('U_E must be positive')
                        rho = exact(config['harvest_rate'])
                        if rho <= 0:
                            raise ValueError('harvest rate must be positive')
                        multiplier = ue*rho/demand
                        rows = [(t['C'],t['D'],t['T'],p*multiplier)
                                for t,p in zip(skeleton,weights)]
                        horizon = max(t['D'] for t in skeleton)
                        for gap in config['harvest_gaps']:
                            integer(gap, 1)
                            beta = [rho*gap*(length//gap) for length in range(horizon+1)]
                            for capacity in config['capacities']:
                                meta = dict(seed=seed, sample=index, target_uc=str(uc),
                                    actual_uc=str(sum(Fraction(t['C'],t['T']) for t in skeleton)/m),
                                    target_ue=str(ue), actual_ue=str(ue), harvest_gap=gap,
                                    harvest_rate=str(rho), power_model=config['power_model'],
                                    generator='legacy_generate_cpu_skeleton_compensated_RM',
                                    workloads=[t['workload'] for t in skeleton])
                                cases.append(make_case(f'm{m}-n{n}-uc{uc}-i{index}-ue{ue}-g{gap}-b{capacity}',
                                    rows,m,beta,capacity=capacity,metadata=meta))
    return cases
