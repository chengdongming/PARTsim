#!/usr/bin/env python3
"""Downloadable reproduction entry point for the unlimited-capacity experiments."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
COMPLETED = {'CERTIFIED', 'NO_CERTIFICATE'}
AUDIT = ['full','no_first_cuts','no_first_work','no_shared_budget','completion_only',
         'last_block_only','progress_only','no_charging_gap','seq_audit']
SUITES = {
    'main': dict(dataset='main', methods=['production_analytic','legacy_audit_full',
                 'legacy_audit_no_shared_budget','seq'], seconds=30, clock='wall', job_order_seed=20261002543),
    'ablation': dict(dataset='latest', methods=AUDIT[:5]+['affine_completion']+AUDIT[5:],
                     seconds=60, extension=300, clock='wall'),
    'constructed': dict(dataset='constructed', methods=AUDIT[:5]+['affine_completion','seq_audit'], seconds=30, clock='wall', serial=True),
    'cost': dict(dataset='audit_timing', methods=['full','affine_completion','completion_only'],
                 seconds=15, repeats=2, serial=True, clock='wall'),
    'cost_branches': dict(dataset='audit_timing', methods=AUDIT, seconds=15, repeats=2, serial=True, clock='wall'),
    'scale': dict(dataset='scale', methods=['full'], seconds=30, serial=True, clock='wall'),
    'quick_coverage': dict(dataset='latest', methods=['production_analytic','deadline','seq'], seconds=15, clock='wall'),
    'quick_timing': dict(dataset='quick_timing', methods=['production_analytic','deadline','fallback'],
                         seconds=30, repeats=3, serial=True, clock='wall'),
    'flow': dict(dataset='flow', methods=['production_analytic','production_flow'],
                 seconds=30, extension=120, extension_max_cases=24, clock='cpu', hash_order=True),
    'flow_timing': dict(dataset='flow_timing', methods=['production_analytic','production_flow'],
                        seconds=None, repeats=3, serial=True, clock='cpu', hash_order=True),
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    os.replace(temporary, path)


def rows(path):
    path = Path(path)
    result = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.exists() else []
    if len({r['request_id'] for r in result}) != len(result):
        raise ValueError(f'Duplicate request IDs in {path}')
    return result


def append(path, row):
    with Path(path).open('a', encoding='utf-8') as stream:
        stream.write(canonical(row)+'\n')
        stream.flush()
        os.fsync(stream.fileno())


def source_identity():
    for rel, item in read(HERE/'runtime/frozen_sources.json').items():
        if hashlib.sha256((HERE/rel).read_bytes()).hexdigest() != item['sha256']:
            raise ValueError(f'Frozen source was modified: {rel}')
    candidates = list(HERE.glob('*.py')) + [HERE/'requirements.txt']
    for name in ('configs','runtime'):
        candidates.extend((HERE/name).rglob('*'))
    files = {str(p.relative_to(HERE)):hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(candidates) if p.is_file() and p.suffix in ('.py','.json','.yml','.txt')
             and '__pycache__' not in p.parts}
    return {'sha256':digest(files), 'files':files}


def environment():
    return {'python':platform.python_version(), 'platform':platform.platform(),
            'affinity':sorted(os.sched_getaffinity(0)), 'PyYAML':importlib.metadata.version('PyYAML'),
            'cpu_model':next((s.split(':',1)[1].strip() for s in Path('/proc/cpuinfo').read_text().splitlines()
                              if s.startswith('model name')), None),
            'cpu_quota':Path('/sys/fs/cgroup/cpu.max').read_text().strip()
                         if Path('/sys/fs/cgroup/cpu.max').exists() else None}


def call(case, method, budget, clock, cpu, job='analysis', answer=None):
    payload = {'case':case, 'method':method, 'budget_seconds':budget, 'clock':clock,
               'cpu':cpu, 'job':job, 'memory_limit_mib':1024}
    if answer is not None:
        payload['answer'] = answer
    wall = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='asapblock-repro-') as folder:
        output = Path(folder)/'answer.json'
        try:
            result = subprocess.run([sys.executable, '-B', str(HERE/'worker.py'), str(output)],
                input=canonical(payload), text=True, capture_output=True,
                timeout=budget if clock == 'wall' else budget*2+25,
                env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'})
            if result.returncode or not output.exists():
                raise RuntimeError(f'Worker failed: {result.returncode}: {result.stderr[-3000:]}')
            value = read(output)
        except subprocess.TimeoutExpired:
            value = {'status':'TIMEOUT', 'censored':True, 'limit_kind':'REQUEST_WALL',
                     'response_bounds':None, 'first_bounds':None}
    value['request_wall_seconds'] = time.perf_counter()-wall
    if value['status'] in ('ERROR', 'RESOURCE_LIMIT'):
        raise RuntimeError(canonical(value))
    if value['status'] not in COMPLETED | {'TIMEOUT','PASS'}:
        raise RuntimeError(f'Unexpected worker outcome: {value["status"]}')
    return value


def load_cases(output, name):
    sys.path.insert(0, str(HERE/'runtime/reproduction/optimized'))
    from experiments.ab_v7_2.data import load_dataset
    return load_dataset(Path(output)/'inputs'/(name+'.json'))


def groups(case):
    return 'new_short_scale' if case['metadata'].get('stage') == 'short_scale' else 'current_long'


def flow_extension(primary, cases, maximum):
    paired = defaultdict(dict)
    for row in primary:
        paired[row['case_id']][row['method']] = row
    cells = defaultdict(list)
    for cid, pair in paired.items():
        if all(r['status'] in COMPLETED for r in pair.values()):
            continue
        c = cases[cid]
        key = (groups(c), c['model']['processors'], len(c['model']['tasks']),
               c['metadata']['deadline_mode'], c['metadata']['harvest_gap'])
        cells[key].append(cid)
    sha = lambda text:hashlib.sha256(text.encode()).hexdigest()
    for ids in cells.values():
        ids.sort(key=lambda cid:(0 if paired[cid]['production_analytic']['status']=='NO_CERTIFICATE'
                                else 2 if paired[cid]['production_analytic']['status']=='CERTIFIED' else 1,
                                sha('extension:'+cid)))
    by_group = {group:sorted([key for key in cells if key[0]==group],
                    key=lambda key:sha('extension-cell:'+json.dumps(key)))
                for group in ('current_long','new_short_scale')}
    keys = [by_group[group][i] for i in range(max(map(len, by_group.values()), default=0))
            for group in sorted(by_group) if i < len(by_group[group])]
    selected = [cells[key][i] for i in range(max(map(len, cells.values()), default=0))
                for key in keys if i < len(cells[key])]
    return selected[:maximum]


def phase(output, suite, config, cases, phase_name, tasks, cpus):
    folder = Path(output)/suite
    log = folder/(phase_name+'.jsonl')
    saved = rows(log)
    planned = {}
    for case, method, repeat, budget in tasks:
        ident = {'run_id':config['run_id'], 'case_id':case['case_id'], 'method':method,
                 'repeat':repeat, 'phase':phase_name, 'budget_seconds':budget}
        planned[digest(ident)] = (ident, case)
    if len(planned) != len(tasks):
        raise ValueError('Duplicate planned request')
    for row in saved:
        if row['request_id'] not in planned or row['run_id'] != config['run_id']:
            raise ValueError('Resume refused: result does not belong to this plan')
        if row['input_sha256'] != planned[row['request_id']][1]['input_sha256']:
            raise ValueError('Resume refused: result input identity differs')
    done = {r['request_id'] for r in saved}
    pending = [(request_id, ident, case) for request_id,(ident,case) in planned.items() if request_id not in done]
    # All methods/repetitions of an input use the same allocated CPU. Timing
    # suites have exactly one queue, and every request gets a fresh process.
    assignments = {cid:i % len(cpus) for i,cid in enumerate(dict.fromkeys(c['case_id'] for _,c in planned.values()))}
    jobs = [[] for _ in cpus]
    for item in pending:
        jobs[assignments[item[2]['case_id']]].append(item)

    def run_queue(cpu, requests):
        for request_id, ident, case in requests:
            value = call(case, ident['method'], ident['budget_seconds'], config['clock'], cpu)
            certificate = value.pop('certificate', None)
            if ident['method'] in ('seq','seq_audit') and value['status']=='CERTIFIED':
                certificate = {k:value[k] for k in ('status','response_bounds','details')}
            value.pop('history', None)
            if certificate is not None:
                cert_path = folder/'certificates'/(request_id+'.json.gz')
                cert_path.parent.mkdir(exist_ok=True)
                with gzip.open(cert_path, 'wt', encoding='utf-8') as stream:
                    stream.write(canonical(certificate))
                value['certificate_file'] = str(cert_path.relative_to(Path(output)))
            meta = case['metadata']
            yield {**ident, **value, 'request_id':request_id, 'input_sha256':case['input_sha256'],
                   'cluster_id':meta.get('cluster_id',case['case_id']), 'stage':meta.get('stage',case['cohort']),
                   'group':groups(case), 'm':case['model']['processors'], 'n':len(case['model']['tasks']),
                   'gap':meta.get('harvest_gap'), 'deadline_mode':meta.get('deadline_mode'),
                   'clock':config['clock'], 'host_cpu':cpu, 'completed_utc':datetime.now(timezone.utc).isoformat()}

    from queue import Queue, Empty
    queue = Queue()
    def consume(cpu, requests):
        try:
            for row in run_queue(cpu, requests):
                queue.put(row)
        finally:
            queue.put(None)
    with ThreadPoolExecutor(max_workers=len(cpus)) as pool:
        futures = [pool.submit(consume, cpu, jobs[i]) for i,cpu in enumerate(cpus)]
        finished = 0
        last_update = time.monotonic()
        while finished < len(cpus):
            try:
                row = queue.get(timeout=1)
            except Empty:
                for future in futures:
                    if future.done() and future.exception():
                        raise future.exception()
                continue
            if row is None:
                finished += 1
                continue
            append(log, row)
            saved.append(row)
            if time.monotonic()-last_update >= 20 or len(saved)==len(tasks):
                print(canonical({'suite':suite, 'phase':phase_name, 'completed':len(saved),
                                 'planned':len(tasks), 'statuses':dict(Counter(r['status'] for r in saved))}), flush=True)
                last_update = time.monotonic()
        for future in futures:
            future.result()
    if len(saved) != len(tasks):
        raise ValueError('Phase incomplete')
    return saved


def suite_plan(output, suite, args):
    spec = dict(SUITES[suite])
    dataset = load_cases(output, spec['dataset'])
    cases = dataset['cases'][:args.limit] if args.limit is not None else dataset['cases']
    if not cases:
        raise ValueError('Empty experiment selection')
    if args.budget_seconds is not None:
        spec['seconds'] = args.budget_seconds
        spec.pop('extension', None)
    if args.methods:
        if set(args.methods)-set(spec['methods']):
            raise ValueError('Methods must be a subset of the chosen suite')
        spec['methods'] = args.methods
        spec.pop('extension', None)
    if suite == 'flow':
        queues = {group:sorted([c for c in cases if groups(c)==group],
                               key=lambda c:hashlib.sha256(('order:'+c['case_id']).encode()).hexdigest())
                  for group in ('current_long','new_short_scale')}
        cases = [queues[group][i] for i in range(max(map(len,queues.values())))
                 for group in ('current_long','new_short_scale') if i < len(queues[group])]
    available = sorted(os.sched_getaffinity(0))
    cpus = available[:1] if spec.get('serial') else (available[1:] or available)[:args.workers]
    spec.update(selected_inputs=len(cases), dataset_sha256=dataset['dataset_sha256'],
                case_ids=[c['case_id'] for c in cases], cpus=cpus, source=source_identity(),
                environment=environment(), suite=suite,
                custom_run=any(v is not None for v in (args.limit, args.budget_seconds, args.methods)))
    spec['run_id'] = digest(spec)
    return spec, cases


def run_suite(output, suite, args):
    folder = Path(output)/suite
    folder.mkdir(parents=True, exist_ok=True)
    spec, cases = suite_plan(output, suite, args)
    path = folder/'plan.json'
    if path.exists():
        if read(path) != spec:
            raise ValueError(f'Resume refused: inputs, sources, options or environment changed for {suite}')
    else:
        write(path, spec)
    selected = {item['case_id']:item for item in read(HERE/'configs/selections.json')['flow_timing']}
    tasks = []
    for repeat in range(spec.get('repeats',1)):
        for index,case in enumerate(cases):
            methods = list(spec['methods'])
            shift = ((int(case['case_id'][-1],16)+repeat) % len(methods)
                     if spec.get('hash_order') else (index+repeat) % len(methods))
            budget = spec['seconds'] if spec['seconds'] is not None else selected[case['case_id']]['budget_cpu_seconds']
            for method in methods[shift:]+methods[:shift]:
                tasks.append((case,method,repeat,budget))
    if spec.get('job_order_seed'):
        import random
        random.Random(spec['job_order_seed']).shuffle(tasks)
    primary = phase(output, suite, spec, cases, 'primary', tasks, spec['cpus'])
    extension = []
    if spec.get('extension'):
        by_case = {c['case_id']:c for c in cases}
        extension_ids = (flow_extension(primary,by_case,spec['extension_max_cases']) if suite=='flow'
                         else sorted({r['case_id'] for r in primary if r['status']=='TIMEOUT'}))
        write(folder/'extension_selection.json', {'case_ids':extension_ids,
              'rule':'Predeclared 24-case stratified targeted extension' if suite=='flow' else 'Every primary TIMEOUT once'})
        jobs = [(by_case[r['case_id']],r['method'],r['repeat'],spec['extension']) for r in primary
                if r['status']=='TIMEOUT' and r['case_id'] in extension_ids]
        extension = phase(output,suite,spec,cases,'extension',jobs,spec['cpus']) if jobs else []
    replacement = {(r['case_id'],r['method'],r['repeat']):r for r in extension}
    final = [replacement.get((r['case_id'],r['method'],r['repeat']),r) for r in primary]
    (folder/'final.jsonl').write_text(''.join(canonical(r)+'\n' for r in final), encoding='utf-8')
    write(folder/'completed.json', {'run_id':spec['run_id'], 'primary_requests':len(primary),
                                   'extension_requests':len(extension), 'final_records':len(final)})
    return final


def verify(output, suites):
    for suite in suites:
        folder = Path(output)/suite
        if not (folder/'completed.json').exists():
            continue
        plan = read(folder/'plan.json')
        if source_identity() != plan['source']:
            raise ValueError('Reference replay refused: sources differ from the run')
        cases = {c['case_id']:c for c in load_cases(output,plan['dataset'])['cases']}
        records = rows(folder/'final.jsonl')
        log = folder/'reference_checks.jsonl'
        saved = rows(log)
        done = {r['request_id'] for r in saved}
        expected = {r['request_id'] for r in records if r['status']=='CERTIFIED'}
        if not done <= expected or any(r['status'] != 'PASS' for r in saved):
            raise ValueError('Reference log does not match certified requests')
        for row in records:
            if row['status'] != 'CERTIFIED' or row['request_id'] in done:
                continue
            with gzip.open(Path(output)/row['certificate_file'], 'rt', encoding='utf-8') as stream:
                answer = json.load(stream)
            check = call(cases[row['case_id']],row['method'],300,'wall',plan['cpus'][0],
                         job='reference',answer=answer)
            if check['status'] != 'PASS':
                raise RuntimeError(f'Original reference replay incomplete: {row["request_id"]}: {check}')
            entry = {'request_id':row['request_id'], 'case_id':row['case_id'], 'method':row['method'], **check}
            append(log, entry)
            saved.append(entry)
        print(canonical({'suite':suite,'reference_checks':len(saved),'status':'PASS'}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare','plan','run','verify','summarize','all'])
    parser.add_argument('--suite', choices=['all']+list(SUITES), default='all')
    parser.add_argument('--output', type=Path, default=HERE/'runs/reproduction')
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--limit', type=int, help='Smoke/custom subset; never labelled a full reproduction')
    parser.add_argument('--budget-seconds', type=float, help='Custom budget; disables extension')
    parser.add_argument('--methods', nargs='+', help='Custom subset of the selected suite methods')
    args = parser.parse_args()
    if not sys.platform.startswith('linux'):
        parser.error('Use Linux or WSL2; CPU affinity and per-process resource limits are required')
    if args.workers < 1 or (args.limit is not None and args.limit < 1):
        parser.error('workers and limit must be positive')
    if args.budget_seconds is not None and (not math.isfinite(args.budget_seconds) or args.budget_seconds <= 0):
        parser.error('budget must be positive and finite')
    if args.methods and len(set(args.methods)) != len(args.methods):
        parser.error('duplicate methods')
    if args.methods and args.suite == 'all':
        parser.error('Choose one suite when specifying a method subset')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    lock = args.output/'reproduction.lock'
    with lock.open('x') as stream:
        stream.write(canonical({'pid':os.getpid(),'action':args.action,'started_utc':datetime.now(timezone.utc).isoformat()}))
    try:
        source_identity()
        suites = list(SUITES) if args.suite=='all' else [args.suite]
        if args.action in ('prepare','plan','run','all'):
            import prepare
            manifest = prepare.prepare(args.output)
            if args.action == 'prepare':
                print(json.dumps(manifest,indent=2))
                return
        if args.action == 'plan':
            for suite in suites:
                spec,_ = suite_plan(args.output,suite,args)
                print(canonical({k:v for k,v in spec.items() if k not in ('source','case_ids','environment')}))
        if args.action in ('run','all'):
            for suite in suites:
                run_suite(args.output,suite,args)
        if args.action in ('verify','all'):
            verify(args.output,suites)
        if args.action in ('summarize','all'):
            import summarize
            print(json.dumps(summarize.summarize(args.output,suites),ensure_ascii=False,indent=2))
    finally:
        lock.unlink()


if __name__ == '__main__':
    main()
