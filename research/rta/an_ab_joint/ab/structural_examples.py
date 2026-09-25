"""Regenerate the small proof examples and their complete certificates."""
from pathlib import Path
import argparse
import json
import ab_structural_rta as sr
import ab_interval_joint as v4
import ab_milestone_rta as ap


def analyze(name, values, m, beta, **options):
    tasks = [sr.model.Task(str(i),*row) for i,row in enumerate(values)]
    answer = sr.least_certificate(tasks,m,beta,**options)
    return dict(name=name, tasks=values,m=m,beta=beta,e0=0,options=options,result=answer)


def examples():
    out=[]
    values=[(1,2,3,1)]*2
    for capacity in (None,1,2):
        out.append(analyze('finite_capacity_threshold',values,2,[0,2,4],
                           capacity=capacity,legacy_v4=False))
    for capacity in (None,1):
        values=[(1,3,4,1)]*2;beta=[0,2,4,6]
        case=analyze('small_battery_progress_and_fixed_window_capacity',values,2,beta,
                     capacity=capacity,legacy_v4=False)
        tasks=[sr.model.Task(str(i),*row) for i,row in enumerate(values)]
        case['capacity_extraction']=sr.capacity_for_certificate(tasks,2,beta,0,case['result']['certificate'])
        out.append(case)
    values=[(1,5,9,1)]*2;beta=[2*(l//2) for l in range(6)]
    case=analyze('zero_single_slot_supply_charging_gap',values,2,beta,
                 capacity=1,legacy_v4=False)
    tasks=[sr.model.Task(str(i),*row) for i,row in enumerate(values)]
    case['capacity_extraction']=sr.capacity_for_certificate(tasks,2,beta,0,case['result']['certificate'])
    case['attaining_trace_contract']=dict(initial_energy=0,releases=[0,0],
                                         periodic_harvest=[0,2],response_times=[3,5])
    out.append(case)
    values=[(1,4,5,3),(2,5,7,1),(1,7,9,2)]
    beta=[4*(l//2) for l in range(8)]
    for bound in ('flow','analytic'):
        out.append(analyze('first_execution_strict_family',values,2,beta,capacity=8,bound=bound))
    values=[(1,4,10,2),(2,5,9,3),(1,6,7,1)]
    beta=[2*l for l in range(7)]
    case=analyze('frozen_holdout_025',values,1,beta)
    tasks=[sr.model.Task(str(i),*row) for i,row in enumerate(values)]
    case['frozen_comparison']={}
    for name,fn in [('V4',v4.least_certificate),('V6_AP',ap.analytic_certificate)]:
        result=fn(tasks,1,beta,0)
        case['frozen_comparison'][name]=dict(status=result['status'],
                   response_bounds=(result.get('certificate') or {}).get('response_bounds'))
    out.append(case)
    out.append(analyze('two_critical_tasks_with_arbitrary_background',
                       [(1,3,3,1),(2,4,5,1)],2,[0,3,6,9,12],capacity=3,background_power=3))
    power=3
    case=analyze('block_vs_bypass_family_P3',[(1,4,4,3)],2,list(range(5)),
                  capacity=3,background_power=1)
    traces={}
    for bypass in (False,True):
        energy,remaining=0,[1,power]
        trace=[]
        for slot in range(power+1):
            before=energy; selected=[]
            for i,cost in enumerate((power,1)):
                if not remaining[i]:continue
                if energy<cost:
                    if bypass:continue
                    break
                energy-=cost;remaining[i]-=1;selected.append(i)
            energy=min(power,energy+1)
            trace.append(dict(slot=slot,energy_before=before,selected=selected,
                              remaining=list(remaining),energy_next=energy))
        traces['ASAP_NONBLOCK' if bypass else 'ASAP_BLOCK']=trace
    case['same_input_witness']=dict(all_tasks=[(1,4,4,3),(3,5,5,1)],
                                    releases=[0,0],initial_energy=0,harvest_per_slot=1,traces=traces)
    out.append(case)
    return dict(version=sr.VERSION,purpose='THEORY_EXAMPLES_NOT_PERFORMANCE_DATA',examples=out)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    data=examples()
    Path(args.output).write_text(json.dumps(data,indent=2,sort_keys=True)+'\n')
    for case in data['examples']:
        result=case['result'];cert=result.get('certificate') or {}
        print(case['name'],case['options'],result['status'],cert.get('profile'))
