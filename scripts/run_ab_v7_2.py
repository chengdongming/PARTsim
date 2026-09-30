#!/usr/bin/env python3
"""Unified V7.2 experiment entrypoint. Examples (from repository root):

  python scripts/run_ab_v7_2.py examples --output outputs/ab-cases.json
  python scripts/run_ab_v7_2.py generate --output outputs/ab-data.json \
      --seed 20260930 --samples 10 --processors 2 --tasks 3 --uc .3,.5 \
      --ue .5,.8 --period-min 8 --period-max 20 --harvest-gaps 1,2
  python scripts/run_ab_v7_2.py run --dataset outputs/ab-data.json \
      --output outputs/ab-performance --suite performance --timeout 60
  python scripts/run_ab_v7_2.py run --dataset outputs/ab-data.json \
      --output outputs/ab-ablation --suite ablation --timeout 60
  python scripts/run_ab_v7_2.py summarize --output outputs/ab-performance --plot

Outputs: frozen dataset, manifest with code hashes, raw JSONL, summary.csv,
pairs.csv, responses.csv and optional SVG plots. Repeat the IDENTICAL run
command with --resume to skip saved requests; changing code/input/settings
requires a new output directory. Interrupted partial JSONL rows fail closed.

All methods use the saved fixed-priority order. SEQ is the original recursive
SEQ kernel, not the research joint-SEQ variant. finite capacity/background
are NOT_APPLICABLE to SEQ. V7.2 always has legacy_v4=False. Seven ablation
modes share the instrumented audit path with production rechecking disabled.
Performance and ablation times have distinct timing_family labels.

Primary certification statistics use repeat zero; timing uses all completed
repeats, separately from timeout/error counts. Mechanism cases are not pooled
with generated inputs. No result is an exact WCRT or infeasibility verdict.
Energy values are exact rationals scaled jointly; release_floor means a lower
bound at EVERY release (default zero), not startup battery charge.

Reviewed legacy integration issues:
* Reuse stable_seed and generate_cpu_skeleton (compensated rounding, exact
  realized-utilization tolerance, stable RM order). Never select by RTA result.
* Old taskset dispatch sorts by period; recursive SEQ here preserves the saved
  order, including the non-RM holdout diagnostic.
* Old beta construction ends at max(D)-1; the shared dataset covers max(D),
  as required by V7.2. Energy scaling changes no time slots or task parameters.
* audit_run formerly fixed e0=0 and reran production in full mode. The extended
  interface keeps both defaults compatible; batch calls disable only the rerun.
* Old runners/plots and all production analysis kernels remain unchanged.

Dependencies: Python >=3.9; PyYAML for generation; Matplotlib only for --plot.
Every output directory has one writer. If a killed runner leaves a .lock file,
confirm its recorded PID has stopped before removing that lock and resuming.
"""
import argparse
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from experiments.ab_v7_2.data import (generate_cases, mechanism_cases, save_dataset)
from experiments.ab_v7_2.adapters import PERFORMANCE, ABLATIONS, METHODS
from experiments.ab_v7_2.runner import run, source_identity
from experiments.ab_v7_2.report import summarize, plot


def csv_ints(text):
    values = [int(v) for v in text.split(',')]
    if not values or any(v < 1 for v in values) or len(set(values)) != len(values):
        raise argparse.ArgumentTypeError('expected distinct positive integers')
    return values


def parser():
    p = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = p.add_subparsers(dest='command',required=True)
    example = commands.add_parser('examples',help='freeze four diagnostic cases, not performance data')
    example.add_argument('--output',type=Path,required=True)
    gen = commands.add_parser('generate',help='reuse legacy compensated CPU task generator')
    gen.add_argument('--output',type=Path,required=True)
    gen.add_argument('--seed',type=int,required=True)
    gen.add_argument('--samples',type=int,default=10)
    gen.add_argument('--processors',type=csv_ints,default=[2])
    gen.add_argument('--tasks',type=csv_ints,default=[3])
    gen.add_argument('--uc',default='0.3,0.5')
    gen.add_argument('--ue',default='0.5,0.8')
    gen.add_argument('--period-min',type=int,default=8)
    gen.add_argument('--period-max',type=int,default=20)
    gen.add_argument('--min-task-util',default='0.01')
    gen.add_argument('--max-task-util',default='0.8')
    gen.add_argument('--tolerance',default='0.05',help='absolute TOTAL utilization tolerance, not per core')
    gen.add_argument('--harvest-rate',default='1')
    gen.add_argument('--harvest-gaps',type=csv_ints,default=[1])
    gen.add_argument('--capacities',default='none',help='exact values in unscaled energy units, or none')
    gen.add_argument('--power-model',choices=('workload','homogeneous'),default='workload')
    gen.add_argument('--system-config',type=Path,default=ROOT/'system_config_unified_template.yml')
    execute = commands.add_parser('run',help='fresh process per method/case/repetition; serial timing')
    execute.add_argument('--dataset',type=Path,required=True)
    execute.add_argument('--output',type=Path,required=True)
    execute.add_argument('--suite',choices=('performance','ablation','all'),default='performance')
    execute.add_argument('--methods',help='explicit comma-separated subset overrides --suite')
    execute.add_argument('--timeout',type=float,default=60.0,help='hard wall seconds per request, including startup')
    execute.add_argument('--repetitions',type=int,default=1)
    execute.add_argument('--resume',action='store_true')
    report = commands.add_parser('summarize',help='summarize existing rows without running analyzers')
    report.add_argument('--output',type=Path,required=True)
    report.add_argument('--plot',action='store_true',help='export SVG panels; requires matplotlib')
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        if args.command == 'examples':
            data = save_dataset(args.output,mechanism_cases(),dict(kind='MECHANISM_ONLY',
                source_sha256=source_identity()['sha256']))
            print(f"Saved {len(data['cases'])} mechanism cases to {args.output}")
        elif args.command == 'generate':
            if args.samples < 1:
                raise ValueError('samples must be positive')
            config = {k:v for k,v in vars(args).items() if k not in ('output','command')}
            config.update(uc=[v.strip() for v in args.uc.split(',')],
                ue=[v.strip() for v in args.ue.split(',')],
                capacities=[None if v.strip().lower()=='none' else v.strip()
                            for v in args.capacities.split(',')],
                system_config=str(args.system_config.resolve()),
                system_config_sha256=hashlib.sha256(args.system_config.read_bytes()).hexdigest(),
                legacy_generator_sha256=hashlib.sha256((ROOT/'global_task_generator.py').read_bytes()).hexdigest(),
                legacy_adapter_sha256=hashlib.sha256((ROOT/'experiments/v9_3/rta_load_cross.py').read_bytes()).hexdigest())
            cases = generate_cases(config)
            save_dataset(args.output,cases,config)
            print(f'Saved {len(cases)} frozen cases to {args.output}')
        elif args.command == 'run':
            methods = tuple(args.methods.split(',')) if args.methods else {
                'performance':PERFORMANCE,'ablation':ABLATIONS,'all':METHODS}[args.suite]
            run(args.dataset,args.output,methods,timeout=args.timeout,
                repetitions=args.repetitions,resume=args.resume)
            result = summarize(args.output)
            print(f"Saved {result['recorded_requests']}/{result['expected_requests']} requests to {args.output}")
        else:
            result = summarize(args.output)
            if args.plot:
                plot(args.output)
            print(f"Summarized {result['recorded_requests']}/{result['expected_requests']} requests")
    except (ValueError, KeyError, OSError) as exc:
        p.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
