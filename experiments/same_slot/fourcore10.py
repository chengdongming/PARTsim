"""Additional, reproducible 4-core/10-task same-slot RTA input cohort.

This is not a mutation of the frozen AB cohorts. Reuse the maintained
UUniFast-Discard and compensated WCET rounding, then impose the exact
D=ceil(0.9T) contract. CPU skeletons and energy weights are held fixed
across UE levels. No selection uses a solver or simulation result.
"""
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import random

import yaml

from experiments.common.global_task_generator import UUniFastDiscard, EnergyAwareTaskGenerator

SCHEMA = "SAME_SLOT_FOURCORE10_DATASET_V1"
M, N = 4, 10
ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT/"configs/templates/system_config_unified_template.yml"


def canonical_config(seed, samples, uc, ue, period_min, period_max, harvest_rate, harvest_gap):
    if any(type(v) is not int for v in (seed,samples,period_min,period_max,harvest_gap)):
        raise ValueError("integer generator parameters required")
    if seed < 0 or samples < 1 or not 1 <= period_min <= period_max or harvest_gap < 1:
        raise ValueError("invalid seed, samples, period range or harvest gap")
    ucs, ues, rate = tuple(map(Fraction,uc)), tuple(map(Fraction,ue)), Fraction(harvest_rate)
    if (not ucs or len(set(ucs))!=len(ucs) or any(not 0<x<=1 for x in ucs)
            or not ues or len(set(ues))!=len(ues) or any(x<=0 for x in ues) or rate<=0):
        raise ValueError("invalid utilization or average harvest rate")
    return dict(seed=seed,samples=samples,uc=list(map(str,ucs)),ue=list(map(str,ues)),
                period_min=period_min,period_max=period_max,
                harvest_rate=str(rate),harvest_gap=harvest_gap)


def _weight_model():
    spec=yaml.safe_load(TEMPLATE.read_text())["energy_management"]["scheduler_energy_model"]
    base=Fraction(str(spec["base_power"]))*Fraction(str(spec["frequency_power_ratios"][8100]))
    return {name:base*Fraction(str(spec["workload_coefficients"][name]))
            for name in ("bzip2","control","decrypt","encrypt","hash")}


def _skeleton(seed, target, lo, hi):
    """Use existing sampler and compensated rounding; filter only input validity."""
    old=random.getstate()
    try:
        random.seed(seed)
        sampler=UUniFastDiscard()
        for trial in range(1000):
            utils=sampler.generate(N,float(M*target),min_task_util=0,
                                   max_task_util=0.9,max_trials=100000)
            T=[random.randint(lo,hi) for _ in range(N)]
            C=EnergyAwareTaskGenerator._compensated_execution_times(
                utils,T,0.9,float(M*target))
            D=[(9*t+9)//10 for t in T]
            actual=sum(Fraction(c,t) for c,t in zip(C,T))/M
            if (all(1<=c<=d<=t for c,d,t in zip(C,D,T))
                    and abs(actual-target)<=Fraction(1,400)):
                workload=[random.choice(("bzip2","control","decrypt","encrypt","hash"))
                          for _ in range(N)]
                order=sorted(range(N),key=lambda i:(T[i],i))
                return [(C[i],D[i],T[i],workload[i]) for i in order],actual,trial+1
        raise ValueError(f"integer UC tolerance not reached for seed {seed}")
    finally:
        random.setstate(old)


def generate(config):
    from .campaign import digest, frozen_data
    from .an_rta import normalize_case
    config=canonical_config(**config)
    weights=_weight_model()
    encoder=frozen_data()
    cases=[]
    rate=Fraction(config["harvest_rate"]);gap=config["harvest_gap"]
    for u in config["uc"]:
        target=Fraction(u)
        for sample in range(config["samples"]):
            seed=int.from_bytes(hashlib.sha256(f'{config["seed"]}|4|10|{u}|{sample}'.encode()).digest()[:8],"big")
            skeleton,actual,trials=_skeleton(seed,target,config["period_min"],config["period_max"])
            demand=sum(Fraction(c,t)*weights[w] for c,d,t,w in skeleton)
            horizon=max(d for c,d,t,w in skeleton)
            beta=[rate*gap*(l//gap) for l in range(horizon+1)]
            for ue in config["ue"]:
                pscale=Fraction(ue)*rate/demand
                rows=[(c,d,t,weights[w]*pscale) for c,d,t,w in skeleton]
                meta=dict(seed=seed,sample=sample,target_uc=u,actual_uc=str(actual),
                          target_ue=ue,actual_ue=ue,deadline_mode="ratio_0.9",
                          priority="RM",power_model="workload",harvest_gap=gap,
                          harvest_rate=str(rate),workloads=[w for c,d,t,w in skeleton],
                          time_factor=1,rounding_trials=trials,
                          cluster_id=f'fourcore10-{config["seed"]}-{u}-{sample}',
                          generator="UUniFast_Discard_compensated_same_slot")
                name=f'{meta["cluster_id"]}-ue{ue}'
                # Only reuse the exact common-denominator encoder, not the
                # old timing or any frozen results/metadata.
                old=encoder.make_case(name,rows,M,beta,release_floor=0,
                                            cohort="fourcore10",metadata=meta)
                model=dict(old["model"],harvest_order="HARVEST_BEFORE_DEBIT",
                           release_floor_stage="PRE_HARVEST")
                source_id=digest(dict(generation=config,metadata=meta,model=model))
                case=dict(name=name,source_case_id=source_id,
                          source_input_sha256=digest(model),source_kind="NEW_FOURCORE10_GENERATOR",
                          metadata=meta,cohort="fourcore10",model=model,
                          input_sha256=digest(model))
                case["case_id"]=digest(case)
                normalize_case(case)
                cases.append(case)
    return cases


def prepare(output, config):
    from .campaign import digest, write_json
    config=canonical_config(**config)
    cases=generate(config)
    data=dict(schema=SCHEMA,generation=config,cases=cases)
    data["dataset_sha256"]=digest(data)
    path=Path(output)/"inputs/fourcore10.json"
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if json.loads(path.read_text())!=data:
            raise ValueError("refusing to overwrite different fourcore10 inputs")
    else:
        write_json(path,data)
    return dict(inputs=len(cases),generation=config,dataset_sha256=data["dataset_sha256"])


def load(path):
    """Independently regenerate the configured cohort; reject edits or filtering."""
    from .campaign import digest
    data=json.loads(Path(path).read_text())
    if data["schema"]!=SCHEMA or data["dataset_sha256"]!=digest(
            {k:v for k,v in data.items() if k!="dataset_sha256"}):
        raise ValueError("fourcore10 dataset schema or hash mismatch")
    expected=generate(data["generation"])
    if data["cases"]!=expected:
        raise ValueError("fourcore10 cases differ from deterministic generation")
    return data
