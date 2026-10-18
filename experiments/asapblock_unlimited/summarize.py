"""Create CSV tables from new runs, retaining censoring and paired denominators."""
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import random
from statistics import mean, median

from reproduce import COMPLETED, canonical, read, rows, write


def csv_file(path, records):
    fields = list(dict.fromkeys(key for r in records for key in r))
    if not fields:
        fields = ['no_eligible_observations']
    with Path(path).open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in records:
            writer.writerow({k:canonical(v) if isinstance(v,(list,dict)) else v for k,v in row.items()})


def stable_cases(records):
    repeated = defaultdict(list)
    for row in records:
        repeated[row['case_id'],row['method']].append(row)
    result = defaultdict(dict)
    for (cid,method), rr in repeated.items():
        # A censored or changing repeat cannot become a completed speed pair.
        statuses = {r['status'] for r in rr}
        row = dict(rr[0])
        row['status'] = next(iter(statuses)) if len(statuses)==1 else 'UNSTABLE'
        row['repeat_count'] = len(rr)
        for clock in ('request_wall_seconds','analysis_wall_seconds','analysis_cpu_seconds'):
            values = [r.get(clock) for r in rr]
            row[clock] = median(values) if all(v is not None for v in values) else None
        if (len({canonical(r.get('response_bounds')) for r in rr}) != 1
                or len({canonical(r.get('first_bounds')) for r in rr}) != 1):
            row['status'] = 'UNSTABLE'
        result[cid][method] = row
    return result


def quantile(values, q):
    values = sorted(values)
    pos = (len(values)-1)*q
    lo = int(pos)
    hi = min(lo+1,len(values)-1)
    return values[lo]+(values[hi]-values[lo])*(pos-lo)


def cluster_summary(values, cases, seed=20261002544, resamples=5000, archived_ablation=False):
    by_cluster = defaultdict(list)
    strata = {}
    for cid,value in sorted(values):
        c = cases[cid]
        cluster = c['metadata'].get('cluster_id',cid)
        by_cluster[cluster].append(value)
        strata[cluster] = (c['metadata'].get('stage'),c['model']['processors'],len(c['model']['tasks']),c['metadata'].get('target_uc'))
    if not values:
        return {'paired_inputs':0,'eligible_cpu_draws':0}
    estimates = {key:mean(v) for key,v in by_cluster.items()}
    groups = defaultdict(list)
    seen = set()
    for c in cases.values():
        key = c['metadata'].get('cluster_id',c['case_id'])
        if key in estimates and key not in seen:
            groups[strata[key]].append(estimates[key])
            seen.add(key)
    rng = random.Random(seed)
    boot = [mean(v[rng.randrange(len(v))] for v in groups.values() for _ in v)
            for _ in range(resamples)]
    interval = ([sorted(boot)[int(q*(len(boot)-1))] for q in (.025,.975)] if archived_ablation
                else [quantile(boot,.025),quantile(boot,.975)])
    return {'paired_inputs':len(values), 'eligible_cpu_draws':len(estimates),
            'equal_draw_mean_percent':mean(estimates.values()),
            'cluster_bootstrap_95_low':interval[0], 'cluster_bootstrap_95_high':interval[1],
            'input_mean_percent':mean(v for _,v in values), 'input_median_percent':median(v for _,v in values),
            'input_min_percent':min(v for _,v in values), 'input_max_percent':max(v for _,v in values),
            'bootstrap_resamples':resamples, 'bootstrap_seed':seed,
            'interval_quantiles':'floor order statistic' if archived_ablation else 'linear interpolation'}


def summarize_suite(output, suite):
    folder = output/suite
    plan = read(folder/'plan.json')
    records = rows(folder/'final.jsonl')
    if len(records) != plan['selected_inputs']*len(plan['methods'])*plan.get('repeats',1):
        raise ValueError('Summary refused: final view is incomplete')
    cases = {c['case_id']:c for c in read(output/'inputs'/(plan['dataset']+'.json'))['cases']}
    pairs = stable_cases(records)
    tables = folder/'tables'
    tables.mkdir(exist_ok=True)
    reference = 'production_analytic' if 'production_analytic' in plan['methods'] else 'full'
    statuses = [{'method':method, 'requests':sum(r['method']==method for r in records),
                 **{status:sum(r['method']==method and r['status']==status for r in records)
                    for status in ('CERTIFIED','NO_CERTIFICATE','TIMEOUT')}} for method in plan['methods']]
    csv_file(tables/'status_counts.csv',statuses)
    pair_counts, response, first, timing = [], [], [], []
    categories = {item['case_id']:item['category'] for item in read(Path(__file__).resolve().parent/'configs/selections.json')['flow_timing']}
    for method in plan['methods']:
        if method == reference:
            continue
        counts = Counter()
        for cid,pair in pairs.items():
            if reference not in pair or method not in pair:
                continue
            a,b = pair[reference],pair[method]
            counts['paired_inputs'] += 1
            if a['status'] not in COMPLETED or b['status'] not in COMPLETED:
                counts['unresolved_or_unstable_pairs'] += 1
                continue
            counts['both_completed'] += 1
            if a['status']=='CERTIFIED' and b['status']=='NO_CERTIFICATE':
                counts['reference_only_definite'] += 1
            elif b['status']=='CERTIFIED' and a['status']=='NO_CERTIFICATE':
                counts['other_only_definite'] += 1
                if suite=='ablation' and method!='seq_audit':
                    raise ValueError(f'Completed ablation reversal: {cid} / {method}')
            if method=='legacy_audit_full' and a['status'] != b['status']:
                raise ValueError(f'Production/audit completed status mismatch: {cid}')
            if a['status']==b['status']=='CERTIFIED':
                counts['both_certified'] += 1
                av,bv = a['response_bounds'],b['response_bounds']
                value = 100*mean((y-x)/y for x,y in zip(av,bv))
                response.append({'case_id':cid,'method':method,'reference':reference,
                    'reference_R_reduction_percent':value,
                    'other_R_reduction_percent':100*mean((x-y)/x for x,y in zip(av,bv))})
                if av == bv:
                    counts['identical_R_vectors'] += 1
                elif all(x<=y for x,y in zip(av,bv)):
                    counts['reference_R_no_larger'] += 1
                elif all(y<=x for x,y in zip(av,bv)):
                    counts['other_R_no_larger'] += 1
                else:
                    counts['crossing_R_vectors'] += 1
                af,bf = a.get('first_bounds'),b.get('first_bounds')
                # CO stores F=R in its audit certificate. For the fair first-unit
                # comparison derive the sequential first-unit bound R-C+1.
                if method == 'completion_only':
                    bf = [r-t['C']+1 for r,t in zip(bv,cases[cid]['model']['tasks'])]
                if method=='legacy_audit_full' and av != bv:
                    raise ValueError(f'Production/audit response mismatch: {cid}')
                if suite=='ablation' and method!='seq_audit':
                    if any(x>y for x,y in zip(av,bv)) or (af and bf and any(x>y for x,y in zip(af,bf))):
                        raise ValueError(f'Completed ablation bound reversal: {cid} / {method}')
                if af and bf:
                    first.append({'case_id':cid,'method':method,'reference':reference,
                                  'reference_F_reduction_percent':100*mean((y-x)/y for x,y in zip(af,bf))})
            clock = 'analysis_wall_seconds' if suite=='flow_timing' else 'request_wall_seconds'
            if a.get(clock) and b.get(clock):
                timing.append({'case_id':cid,'method':method,'reference':reference,
                    'reference_status':a['status'],'other_status':b['status'],
                    'category':categories.get(cid) if suite=='flow_timing' else None,
                    'reference_seconds':a[clock], 'other_seconds':b[clock],
                    'reference_over_other':a[clock]/b[clock], 'other_over_reference':b[clock]/a[clock],
                    'clock':clock,'repetitions':a['repeat_count']})
        pair_counts.append({'reference':reference,'other':method,
            **{k:counts[k] for k in ('paired_inputs','both_completed','both_certified','reference_only_definite',
                                    'other_only_definite','unresolved_or_unstable_pairs','identical_R_vectors',
                                    'reference_R_no_larger','other_R_no_larger','crossing_R_vectors')}})
    csv_file(tables/'paired_counts.csv',pair_counts)
    csv_file(tables/'response_by_input.csv',response)
    csv_file(tables/'first_unit_by_input.csv',first)
    csv_file(tables/'timing_by_input.csv',timing)
    precision = []
    precision_inputs = [('R',response,'reference_R_reduction_percent'),('F',first,'reference_F_reduction_percent')]
    for kind, observations, value_key in precision_inputs if suite in ('main','ablation') else []:
        for method in plan['methods']:
            eligible = [r for r in observations if r['method']==method]
            for stage in ['all']+sorted({cases[r['case_id']]['metadata'].get('stage',cases[r['case_id']]['cohort']) for r in eligible}):
                values = [(r['case_id'],r[value_key]) for r in eligible
                          if stage=='all' or cases[r['case_id']]['metadata'].get('stage',cases[r['case_id']]['cohort'])==stage]
                if values:
                    precision.append({'bound':kind,'reference':reference,'other':method,'stage':stage,
                        **cluster_summary(values,cases,seed=20261014 if suite=='ablation' else 20261002544,
                                          archived_ablation=suite=='ablation')})
    csv_file(tables/'precision_cluster_summary.csv',precision)
    timing_summary = []
    for method in plan['methods']:
        for stratum in ('all_completed','same_status','both_certified','both_no_certificate'):
            vv = [r for r in timing if r['method']==method and (
                stratum=='all_completed' or stratum=='same_status' and r['reference_status']==r['other_status']
                or stratum=='both_certified' and r['reference_status']==r['other_status']=='CERTIFIED'
                or stratum=='both_no_certificate' and r['reference_status']==r['other_status']=='NO_CERTIFICATE')]
            if suite=='flow_timing':
                vv = [r for r in vv if r['category'] in ('common_certified','common_no_certificate')]
            if vv:
                timing_summary.append({'reference':reference,'other':method,'stratum':stratum,'eligible_inputs':len(vv),
                    'median_reference_over_other':median(r['reference_over_other'] for r in vv),
                    'median_other_over_reference':median(r['other_over_reference'] for r in vv),
                    'min_other_over_reference':min(r['other_over_reference'] for r in vv),
                    'max_other_over_reference':max(r['other_over_reference'] for r in vv),
                    'time_definition':vv[0]['clock']})
    csv_file(tables/'timing_summary.csv',timing_summary)
    strata = defaultdict(Counter)
    for row in records:
        c = cases[row['case_id']]
        key = (row['method'],row['stage'],row['m'],row['n'],row.get('gap'),c['metadata'].get('target_uc'))
        strata[key][row['status']] += 1
    csv_file(tables/'counts_by_configuration.csv',[{'method':k[0],'stage':k[1],'M':k[2],'N':k[3],
        'gap':k[4],'target_uc':k[5],**dict(v)} for k,v in strata.items()])
    if suite=='scale':
        csv_file(tables/'scale_points.csv',[{'case_id':r['case_id'],'M':r['m'],'N':r['n'],
            'time_factor':cases[r['case_id']]['metadata']['time_factor'],
            'max_deadline':max(t['D'] for t in cases[r['case_id']]['model']['tasks']),
            'status':r['status'],'request_wall_seconds':r['request_wall_seconds']} for r in records])
    if suite=='flow':
        primary = rows(folder/'primary.jsonl')
        csv_file(tables/'primary_status_counts.csv',[{'method':m,**dict(Counter(r['status'] for r in primary if r['method']==m))}
                                                     for m in plan['methods']])
        improvements = [r['other_R_reduction_percent'] for r in response if r['method']=='production_flow']
        csv_file(tables/'flow_response_summary.csv',[{'common_certified_inputs':len(improvements),
            'mean_input_flow_R_reduction_percent':mean(improvements),
            'median_input_flow_R_reduction_percent':median(improvements),
            'minimum_input_flow_R_reduction_percent':min(improvements),
            'maximum_input_flow_R_reduction_percent':max(improvements)}] if improvements else [])
    references = rows(folder/'reference_checks.jsonl')
    summary = {'suite':suite,'custom_run':plan['custom_run'],'selected_inputs':plan['selected_inputs'],
               'status_counts':statuses,'paired_counts':pair_counts,
               'original_reference_checks':len(references),
               'reference_checks_pending':sum(r['status']=='CERTIFIED' and 'certificate_file' in r for r in records)-len(references),
               'legacy_audit_without_exported_certificate':sum(r['status']=='CERTIFIED' and 'certificate_file' not in r for r in records),
               'statistical_unit':'CPU draw; related variants remain clustered',
               'timing_scope':'Comparative timing uses serial suites only; no timeout is replaced by its budget',
               'tables_directory':str(tables)}
    write(folder/'summary.json',summary)
    return summary


def summarize(output, suites):
    output = Path(output)
    return [summarize_suite(output,suite) for suite in suites if (output/suite/'completed.json').exists()]
