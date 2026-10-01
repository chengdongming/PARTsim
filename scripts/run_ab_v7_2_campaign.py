#!/usr/bin/env python3
"""Publication and qualification profiles for ASAPBlock V7.2.

  python scripts/run_ab_v7_2_campaign.py plan --config configs/ab_v7_2/publication.json
  python scripts/run_ab_v7_2_campaign.py prepare --config configs/ab_v7_2/qualification.json --output outputs/ab-qualification
  python scripts/run_ab_v7_2_campaign.py run --output outputs/ab-qualification --stage calibration
  python scripts/run_ab_v7_2_campaign.py run --output outputs/ab-qualification --stage qualification,flow_check,timing_check,time_scale_check
  python scripts/run_ab_v7_2_campaign.py summarize --output outputs/ab-qualification

For formal data use a new directory for each fixed sample range. Example:
  ... prepare --config configs/ab_v7_2/publication.json --stage main_cpu --start 0 --count 10 --output outputs/paper-main-0000
Repeat with start=10,20,...,990; never pool qualification/old pilot data.
run --resume keeps saved requests under identical frozen code/config/environment.
Do not edit the code or output files while a writer is active.
main_cpu holds normalized U_E fixed by rescaling powers. fixed_power_cpu instead
holds workload coefficients fixed, varies CPU load, and reports actual U_E.
fixed_power_qualification.json checks that second interpretation separately.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from experiments.ab_v7_2.campaign import prepare,execute,campaign_summary,counts

def main():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('action',choices=['plan','prepare','run','summarize','combine'])
    p.add_argument('--config',type=Path)
    p.add_argument('--output',type=Path)
    p.add_argument('--stage',help='comma separated stages; default all in frozen profile')
    p.add_argument('--start',type=int,default=0)
    p.add_argument('--count',type=int)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--inputs',help='comma separated disjoint campaign directories for combine')
    args=p.parse_args()
    try:
        stages=args.stage.split(',') if args.stage else None
        if args.action in ('plan','prepare'):
            if not args.config:p.error('--config required')
            config=json.loads(args.config.read_text());stages=stages or list(config['stages'])
            if args.action=='plan':
                values=[counts(config,stage,args.count) for stage in stages]
                print(json.dumps(dict(stages=values,total_requests=sum(s['requests'] for s in values)),indent=2));return
            if not args.output:p.error('--output required')
            result=prepare(args.config,args.output,stages,start=args.start,count=args.count)
            print(json.dumps(result['stages'],indent=2))
        elif args.action=='run':
            if not args.output:p.error('--output required')
            execute(args.output,stages=stages,resume=args.resume)
        else:
            if not args.output:p.error('--output required')
            if args.action=='combine' and not args.inputs:p.error('--inputs required')
            result=campaign_summary(args.output,args.inputs.split(',') if args.action=='combine' else None)
            print(json.dumps(result['stages'],indent=2))
    except (ValueError,KeyError,OSError) as exc:p.error(str(exc))

if __name__=='__main__':main()
