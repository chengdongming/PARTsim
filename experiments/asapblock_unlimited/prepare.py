"""Regenerate the frozen experiment inputs from seeds; no result files needed."""
from collections import defaultdict
from itertools import product
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'runtime'))
import generator
from experiments.ab_v7_2 import data


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def identity(cases):
    pairs = sorted((c['case_id'], c['input_sha256']) for c in cases)
    return hashlib.sha256(json.dumps(pairs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def generate_campaign(config):
    result = []
    for stage in config['stages']:
        spec = generator.settings(config, stage)
        result.extend(generator.generate(config, stage, 0, spec['samples']))
    return result


def constructed():
    cases = []
    for period, scale in product([7, 8, 9, 17, 101, 1000], [1, 2, 7]):
        cases.append(data.make_case(f'family-T{period}-scale{scale}',
            [(1,4,5,3*scale),(2,5,7,scale),(1,7,period,2*scale)],
            2, [4*scale*(length//2) for length in range(8)],
            cohort='constructed_family', metadata={'family':'manuscript_4_4','T3':period,'scale':scale}))
    for p1, p2, p3, burst, gap in product([2,3,4], [1,2], [1,2,3], [3,4,5], [1,2,3]):
        cases.append(data.make_case(f'neighbour-p{p1}-{p2}-{p3}-B{burst}-G{gap}',
            [(1,4,5,p1),(2,5,7,p2),(1,7,9,p3)],
            2, [burst*(length//gap) for length in range(8)],
            cohort='constructed_neighbourhood', metadata={'p1':p1,'p2':p2,'p3':p3,'burst':burst,'gap':gap}))
    return cases


def flow_inputs(latest, fresh):
    cells = defaultdict(lambda: defaultdict(list))
    for case in latest:
        m, meta = case['model'], case['metadata']
        cells[m['processors'], len(m['tasks']), meta['target_uc']][meta['cluster_id']].append(case)
    selected = []
    for cell in sorted(cells):
        ids = sorted(cells[cell], key=lambda cid: hashlib.sha256(
            ('AB_EXACT_SCALE_20261017:' + cid).encode()).hexdigest())[:2]
        for cid in ids:
            selected.extend(cells[cell][cid])
    return selected + fresh


def build_inputs():
    configs = read(HERE/'configs/campaigns.json')
    selection = read(HERE/'configs/selections.json')
    main = generate_campaign(configs['main'])
    for case in main:
        case['metadata'].update(configs['main_metadata'])
        case['case_id'] = data.digest({k:v for k,v in case.items() if k != 'case_id'})
    latest = generate_campaign(configs['latest'])
    scale_all = generate_campaign(configs['scale'])
    flow = flow_inputs(latest, generate_campaign(configs['flow_fresh']))
    sets = {'main':main, 'latest':latest, 'constructed':constructed(), 'flow':flow, 'scale_all':scale_all}
    expected = read(HERE/'configs/expected_inputs.json')
    for name, cases in sets.items():
        for case in cases:
            data.validate_case(case)
        actual = {'inputs':len(cases), 'identity_sha256':identity(cases)}
        if actual != expected[name]:
            raise ValueError(f'Frozen input mismatch for {name}: expected {expected[name]}, got {actual}')
    by_latest = {c['case_id']:c for c in latest}
    by_scale = {c['case_id']:c for c in scale_all}
    by_flow = {c['case_id']:c for c in flow}
    sets['audit_timing'] = [by_latest[cid] for cid in selection['audit_timing']]
    sets['quick_timing'] = [by_latest[cid] for cid in selection['quick_timing']]
    sets['scale'] = [by_scale[cid] for cid in selection['scale']]
    sets['flow_timing'] = [by_flow[item['case_id']] for item in selection['flow_timing']]
    return sets


def prepare(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    datasets = output/'inputs'
    datasets.mkdir(exist_ok=True)
    sets = build_inputs()
    manifest = {}
    for name, cases in sets.items():
        path = datasets/(name+'.json')
        value = {'schema':data.SCHEMA, 'generation':{'code':'asapblock_unlimited','cohort':name}, 'cases':cases}
        value['dataset_sha256'] = data.digest(value)
        if path.exists():
            if read(path) != value:
                raise ValueError(f'Refusing to overwrite different inputs: {path}')
        else:
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        manifest[name] = {'inputs':len(cases), 'identity_sha256':identity(cases),
                          'dataset_sha256':value['dataset_sha256']}
    (output/'input_manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output), indent=2))
