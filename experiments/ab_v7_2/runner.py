"""One subprocess per request, hard wall timeout, durable rows and strict resume.

analysis_wall_seconds excludes interpreter startup/import/input conversion;
request_wall_seconds includes them and is the hard-timeout budget. Timeout
rows have no fabricated analysis/CPU/memory measurement. They are censored,
never averaged as completed analysis times. Runs are serial for timing.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import traceback

from .data import ROOT, canonical, digest, load_dataset, validate_case
from .adapters import METHODS, method_config, applicable, prepare


def source_identity():
    paths = sorted(set(ROOT.glob('asap_block_rta_v9_3*.py')) |
        set((ROOT/'research/rta/an_ab_joint/ab').rglob('*.py')) |
        set((ROOT/'experiments/ab_v7_2').glob('*.py')) |
        {ROOT/'scripts/run_ab_v7_2.py'})
    hashes = {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    return dict(sha256=digest(hashes), files=hashes)


def environment():
    git = subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,text=True,capture_output=True)
    dirty = subprocess.run(['git','status','--porcelain'],cwd=ROOT,text=True,capture_output=True)
    return dict(python=sys.version, executable=sys.executable, platform=platform.platform(),
        machine=platform.machine(), cpu_count=os.cpu_count(),
        affinity=sorted(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else None,
        git_commit=git.stdout.strip(), git_dirty=bool(dirty.stdout.strip()))


def worker(payload):
    """Executed only via python -m ...runner; no inherited modules/caches."""
    case, method = payload['case'], payload['method']
    validate_case(case)
    if not applicable(case,method):
        return dict(status='NOT_APPLICABLE', reason='SEQ requires no overflow and no arbitrary background',
                    response_bounds=None, first_bounds=None)
    call = prepare(case,method)
    wall, cpu = time.perf_counter(), time.process_time()
    try:
        answer = call()
    except Exception:
        answer = dict(status='ERROR', reason=traceback.format_exc(),response_bounds=None,first_bounds=None)
    answer.update(analysis_wall_seconds=time.perf_counter()-wall,
                  analysis_cpu_seconds=time.process_time()-cpu)
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        answer['process_peak_rss_bytes'] = int(value if sys.platform == 'darwin' else value*1024)
    except ImportError:
        answer['process_peak_rss_bytes'] = None
    return answer


def execute(case, method, timeout=60.0):
    if method not in METHODS or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('invalid method/timeout')
    base = dict(case_id=case['case_id'],input_sha256=case['input_sha256'],
        name=case['name'],cohort=case['cohort'],config=method_config(method),
        scope='CRITICAL_PREFIX' if case['model']['background_power'] else 'ALL_TASKS',
        analysis_wall_seconds=None,analysis_cpu_seconds=None,process_peak_rss_bytes=None,
        response_bounds=None,first_bounds=None,timeout_seconds=timeout)
    if not applicable(case, method):
        return dict(base,status='NOT_APPLICABLE',request_wall_seconds=0.0,
                    reason='SEQ requires no overflow and no arbitrary background')
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='ab-v72-') as temporary:
        output = Path(temporary)/'result.json'
        try:
            child = subprocess.run([sys.executable,'-m','experiments.ab_v7_2.runner',str(output)],
                input=canonical(dict(case=case,method=method)),text=True,capture_output=True,
                cwd=ROOT,timeout=timeout)
            if child.returncode or not output.exists():
                result = dict(status='ERROR',reason=f'worker exit {child.returncode}: {child.stderr[-4000:]}')
            else:
                result = json.loads(output.read_text())
        except subprocess.TimeoutExpired:
            result = dict(status='TIMEOUT',reason='hard request wall-time limit',
                          censored=True)
    return dict(base,**(result | {'request_wall_seconds':time.perf_counter()-started}))


def read_rows(path):
    """Reject incomplete or duplicate rows; never silently resume past corruption."""
    if not Path(path).exists():
        return []
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    ids = [r['request_id'] for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate request IDs in result file')
    return rows


def run(dataset_path, output, methods, *, timeout=60.0, repetitions=1, resume=False):
    """Reject concurrent writers, including two simultaneous resume commands.

    An unclean termination can leave the sibling .lock file; inspect its PID
    and remove it only after confirming that the old runner has stopped.
    """
    output = Path(output).resolve()
    output.parent.mkdir(parents=True,exist_ok=True)
    lock = output.with_name(output.name+'.lock')
    with lock.open('x') as stream:
        stream.write(str(os.getpid()))
    try:
        return _run(dataset_path,output,methods,timeout=timeout,
                    repetitions=repetitions,resume=resume)
    finally:
        lock.unlink()


def _run(dataset_path, output, methods, *, timeout, repetitions, resume):
    data = load_dataset(dataset_path)
    if (not methods or len(set(methods)) != len(methods) or set(methods)-set(METHODS)
            or type(repetitions) is not int or repetitions < 1
            or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError('invalid run configuration')
    source = source_identity()
    config = dict(dataset_sha256=data['dataset_sha256'],methods=list(methods),
        timeout_seconds=timeout,repetitions=repetitions,source_sha256=source['sha256'],
        method_configs=[method_config(m) for m in methods],
        timing='SERIAL_FRESH_PROCESS_HARD_REQUEST_WALL_LIMIT',schema='AB_V7_2_RUN_1')
    output = Path(output)
    manifest_path, results_path = output/'manifest.json', output/'results.jsonl'
    host = environment()
    if resume:
        manifest = json.loads(manifest_path.read_text())
        if manifest['config'] != config or manifest['environment'] != host:
            raise ValueError('resume refused: dataset, code, configuration or environment changed')
        if load_dataset(output/'dataset.json')['dataset_sha256'] != data['dataset_sha256']:
            raise ValueError('saved dataset mismatch')
    else:
        output.mkdir(parents=True,exist_ok=False)
        manifest = dict(config=config,environment=host,source=source,
            created_utc=datetime.now(timezone.utc).isoformat())
        manifest_path.write_text(json.dumps(manifest,indent=2))
        (output/'dataset.json').write_text(json.dumps(data,indent=2))
    run_id = digest(config)
    requests = []
    for index,case in enumerate(data['cases']):
        for repeat in range(repetitions):
            shift = (index+repeat)%len(methods)
            order = list(methods[shift:])+list(methods[:shift])
            for method in order:
                key = dict(run_id=run_id,case_id=case['case_id'],method=method,repetition=repeat)
                requests.append((digest(key),case,method,repeat))
    saved = read_rows(results_path)
    planned = {r[0]:(r[1]['case_id'],r[2],r[3]) for r in requests}
    for row in saved:
        if planned.get(row['request_id']) != (row['case_id'],row['config']['method'],row['repetition']):
            raise ValueError('result row does not match the run plan')
    done = {r['request_id'] for r in saved}
    with results_path.open('a',encoding='utf-8') as stream:
        for request_id,case,method,repeat in requests:
            if request_id in done:
                continue
            row = execute(case,method,timeout)
            row.update(request_id=request_id,run_id=run_id,repetition=repeat)
            stream.write(canonical(row)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
            print(f"{case['name']} {method} #{repeat}: {row['status']}",flush=True)
    return output


if __name__ == '__main__':
    try:
        result = worker(json.load(sys.stdin))
    except Exception:
        result = dict(status='ERROR',reason=traceback.format_exc())
    Path(sys.argv[1]).write_text(json.dumps(result,allow_nan=False))
