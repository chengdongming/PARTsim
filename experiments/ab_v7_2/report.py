"""Paired summaries; no-certificate is NOT a schedulability counterexample.

Certification rates and response comparisons use repetition zero, so repeats
do not inflate sample counts. Completed runtimes use every repeat and are
explicitly conditional on completion. Timeouts/errors/unsupported models are
reported separately. Mechanism and generated cohorts are never pooled.
"""
from collections import Counter, defaultdict
import csv
from fractions import Fraction
from itertools import combinations
import json
from pathlib import Path
import statistics

from .data import canonical, digest, load_dataset
from .runner import read_rows


def cell(case):
    model, meta = case['model'], case['metadata']
    scale = model['energy_scale']
    return dict(cohort=case['cohort'],processors=model['processors'],tasks=len(model['tasks']),
        capacity=None if model['capacity'] is None else str(Fraction(model['capacity'],scale)),
        background_power=str(Fraction(model['background_power'],scale)),
        release_floor=str(Fraction(model['release_floor'],scale)),
        **{key:meta.get(key) for key in ('target_uc','target_ue','harvest_gap','power_model')})


def write_csv(path, rows):
    if not rows:
        path.write_text('')
        return
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w',newline='',encoding='utf-8') as f:
        writer = csv.DictWriter(f,fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def summarize(output):
    output = Path(output)
    data = load_dataset(output/'dataset.json')
    manifest = json.loads((output/'manifest.json').read_text())
    config = manifest['config']
    if data['dataset_sha256'] != config['dataset_sha256']:
        raise ValueError('manifest/dataset mismatch')
    cases = {c['case_id']:c for c in data['cases']}
    rows = read_rows(output/'results.jsonl')
    groups, primary, responses = defaultdict(list), {}, []
    allowed = {'CERTIFIED','NO_CERTIFICATE','TIMEOUT','ERROR','NOT_APPLICABLE'}
    run_id = digest(config)
    for row in rows:
        method = row['config']['method']
        if (row['case_id'] not in cases or method not in config['methods']
                or row['status'] not in allowed or row['run_id'] != run_id
                or not 0 <= row['repetition'] < config['repetitions']):
            raise ValueError('result outside manifest plan')
        expected_id = digest(dict(run_id=run_id,case_id=row['case_id'],method=method,
                                  repetition=row['repetition']))
        if row['request_id'] != expected_id:
            raise ValueError('request identity mismatch')
        case = cases[row['case_id']]
        if row['input_sha256'] != case['input_sha256']:
            raise ValueError('result/input mismatch')
        if row['status'] == 'CERTIFIED':
            bounds = row['response_bounds']
            if len(bounds) != len(case['model']['tasks']) or any(
                type(r) is not int or not t['C'] <= r <= t['D']
                for r,t in zip(bounds,case['model']['tasks'])):
                raise ValueError('invalid certified response vector')
        groups[(canonical(cell(case)),method)].append(row)
        if row['repetition'] == 0:
            primary[(row['case_id'],method)] = row
            if row['status'] == 'CERTIFIED':
                for i,(task,response) in enumerate(zip(case['model']['tasks'],row['response_bounds'])):
                    responses.append(dict(case_id=case['case_id'],name=case['name'],method=method,
                        cohort=case['cohort'],task=task['name'],C=task['C'],D=task['D'],T=task['T'],
                        R=response,F=(row['first_bounds'][i] if row.get('first_bounds') else None)))
    agreement_checks = 0
    for cid in cases:
        production, audit = primary.get((cid,'v72_analytic')), primary.get((cid,'ablation_full'))
        if (production and audit and production['status'] in ('CERTIFIED','NO_CERTIFICATE')
                and audit['status'] in ('CERTIFIED','NO_CERTIFICATE')):
            keys = ('status','response_bounds','first_bounds')
            if any(production.get(k) != audit.get(k) for k in keys):
                raise ValueError(f'full ablation/production mismatch: {cases[cid]["name"]}')
            agreement_checks += 1
    summaries = []
    for (cell_json,method), group in groups.items():
        # Repetitions may timeout, but completed deterministic results must agree.
        observed = defaultdict(set)
        for r in group:
            if r['status'] in ('CERTIFIED','NO_CERTIFICATE'):
                observed[r['case_id']].add(canonical([r['status'],r['response_bounds'],r.get('first_bounds')]))
        if any(len(values)>1 for values in observed.values()):
            raise ValueError('inconsistent completed repetitions')
        counts = Counter(r['status'] for r in group if r['repetition'] == 0)
        applicable = sum(v for k,v in counts.items() if k != 'NOT_APPLICABLE')
        completed = [r['analysis_wall_seconds'] for r in group
                     if r['status'] in ('CERTIFIED','NO_CERTIFICATE') and r['analysis_wall_seconds'] is not None]
        request_times = [r['request_wall_seconds'] for r in group
                         if r['status'] in ('CERTIFIED','NO_CERTIFICATE') and r.get('request_wall_seconds') is not None]
        rss = [r['process_peak_rss_bytes'] for r in group
               if r['status'] in ('CERTIFIED','NO_CERTIFICATE') and r.get('process_peak_rss_bytes') is not None]
        summaries.append(dict(**json.loads(cell_json),method=method,
            timing_family=group[0]['config']['timing_family'],primary_cases=sum(counts.values()),
            applicable_primary_cases=applicable,certified=counts['CERTIFIED'],
            no_certificate=counts['NO_CERTIFICATE'],timeout=counts['TIMEOUT'],
            error=counts['ERROR'],not_applicable=counts['NOT_APPLICABLE'],
            certified_fraction_under_budget=counts['CERTIFIED']/applicable if applicable else None,
            timeout_fraction_primary=counts['TIMEOUT']/applicable if applicable else None,
            total_requests=len(group),timeout_requests=sum(r['status']=='TIMEOUT' for r in group),
            completed_timing_requests=len(completed),
            median_completed_analysis_seconds=statistics.median(completed) if completed else None,
            median_completed_request_seconds=statistics.median(request_times) if request_times else None,
            max_completed_process_peak_rss_bytes=max(rss) if rss else None))
    pairs = []
    cells = {canonical(cell(c)) for c in cases.values()}
    for cell_json in sorted(cells):
        ids = [cid for cid,c in cases.items() if canonical(cell(c)) == cell_json]
        for a,b in combinations(config['methods'],2):
            matched = [(primary[(cid,a)],primary[(cid,b)]) for cid in ids
                       if (cid,a) in primary and (cid,b) in primary]
            valid = [(x,y) for x,y in matched if x['status']!='NOT_APPLICABLE' and y['status']!='NOT_APPLICABLE']
            solved = [(x,y) for x,y in valid if x['status'] in ('CERTIFIED','NO_CERTIFICATE')
                      and y['status'] in ('CERTIFIED','NO_CERTIFICATE')]
            common = [(x,y) for x,y in solved if x['status']==y['status']=='CERTIFIED']
            ratios = [statistics.mean(rx/ry for rx,ry in zip(x['response_bounds'],y['response_bounds']))
                      for x,y in common]
            pairs.append(dict(**json.loads(cell_json),method_a=a,method_b=b,
                matched_primary_cases=len(matched),applicable_pairs=len(valid),
                both_completed=len(solved),unresolved_pairs=len(valid)-len(solved),
                both_certified=len(common),
                a_only_vs_completed_b=sum(x['status']=='CERTIFIED' and y['status']=='NO_CERTIFICATE' for x,y in solved),
                b_only_vs_completed_a=sum(y['status']=='CERTIFIED' and x['status']=='NO_CERTIFICATE' for x,y in solved),
                mean_case_response_ratio_a_over_b=statistics.mean(ratios) if ratios else None))
    expected = len(cases)*len(config['methods'])*config['repetitions']
    result = dict(complete=len(rows)==expected,expected_requests=expected,recorded_requests=len(rows),
        certification_repetition=0,full_production_agreement_checks=agreement_checks,
        summaries=summaries,pairs=pairs)
    (output/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    write_csv(output/'summary.csv',summaries)
    write_csv(output/'pairs.csv',pairs)
    write_csv(output/'responses.csv',responses)
    return result


def plot(output):
    """Optional Matplotlib export, one panel per parameter cell and timing family."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    output = Path(output)
    result = summarize(output)
    grouped = defaultdict(list)
    for row in result['summaries']:
        keys = ('cohort','processors','tasks','capacity','background_power','release_floor',
                'target_uc','target_ue','harvest_gap','power_model','timing_family')
        grouped[canonical({k:row[k] for k in keys})].append(row)
    for index,(label,group) in enumerate(sorted(grouped.items())):
        group = [r for r in group if r['applicable_primary_cases']]
        if not group:
            continue
        fig,axes = plt.subplots(1,2,figsize=(13,5),layout='constrained')
        names = [r['method'] for r in group]
        axes[0].bar(names,[r['certified_fraction_under_budget'] for r in group])
        axes[0].set(ylabel='Certified fraction under budget',ylim=(0,1))
        timed = [r for r in group if r['median_completed_analysis_seconds'] is not None]
        axes[1].bar([r['method'] for r in timed],[r['median_completed_analysis_seconds'] for r in timed])
        axes[1].set(ylabel='Median analysis seconds (completed only)',yscale='log')
        for axis in axes:
            axis.tick_params(axis='x',labelrotation=65)
        fig.suptitle(label,fontsize=8,wrap=True)
        fig.savefig(output/f'cell_{index:03d}.svg')
        plt.close(fig)
