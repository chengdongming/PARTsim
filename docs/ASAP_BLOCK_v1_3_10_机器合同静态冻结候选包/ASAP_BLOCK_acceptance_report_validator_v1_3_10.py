#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import argparse, ast, json, os, re, subprocess, sys, tempfile, textwrap, unicodedata
sys.dont_write_bytecode=True
from ASAP_BLOCK_validation_common_v1_3_10 import *
VERSION='1.3.10'
REPLAY_INTERFACE='ASAP_BLOCK_GATE_REPLAY_V1'
EVIDENCE_DOMAIN='ASAP_BLOCK:GATE_EVIDENCE_BUNDLE:v1.3.10'
ALLOWED_AST=(ast.Expression,ast.BoolOp,ast.BinOp,ast.UnaryOp,ast.Compare,ast.Name,ast.Load,ast.Constant,ast.And,ast.Or,ast.Not,ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE,ast.Add,ast.Sub,ast.Mult,ast.Div,ast.Mod)
CANONICAL_GATE_SPECS={'CORE0A_gates': {'event_order_and_scheduler_semantics_microcases': {'count_keys': ['N_failures', 'N_microcases'],
                                                                     'predicate': 'N_microcases > 0 and N_failures == '
                                                                                  '0',
                                                                     'required': True},
                  'exact_exhaustive_domain_zero_mismatch': {'count_keys': ['N_exhaustive_instances', 'N_mismatches'],
                                                            'predicate': 'N_exhaustive_instances > 0 and N_mismatches '
                                                                         '== 0',
                                                            'required': True},
                  'exact_random_boundary_instances_at_least_10000': {'count_keys': ['N_mismatches',
                                                                                    'N_random_boundary_instances'],
                                                                     'predicate': 'N_random_boundary_instances >= '
                                                                                  '10000 and N_mismatches == 0',
                                                                     'required': True},
                  'finite_state_counterexample_search': {'count_keys': ['N_certified_bound_violations',
                                                                        'N_complete',
                                                                        'N_inconclusive',
                                                                        'N_instances',
                                                                        'N_internal_error'],
                                                         'predicate': 'N_complete > 0 and N_inconclusive == 0 and '
                                                                      'N_internal_error == 0 and '
                                                                      'N_certified_bound_violations == 0',
                                                         'required': True},
                  'full_w_q_h_scan': {'count_keys': ['N_scan_violations', 'N_tasks_checked'],
                                      'predicate': 'N_scan_violations == 0 and N_tasks_checked > 0',
                                      'required': True},
                  'joint_certification_state_machine': {'count_keys': ['N_failures', 'N_state_cases'],
                                                        'predicate': 'N_state_cases > 0 and N_failures == 0',
                                                        'required': True},
                  'loc_theta_cw_dominance_invariant': {'count_keys': ['N_common_cases', 'N_violations'],
                                                       'predicate': 'N_common_cases > 0 and N_violations == 0',
                                                       'required': True},
                  'non_vacuity_coverage': {'count_keys': ['N_certified_tasksets',
                                                          'N_complete_local_common_cases',
                                                          'N_energy_blocking_cases',
                                                          'N_positive_E0_satisfied_traces',
                                                          'N_processor_interference_cases'],
                                           'predicate': 'N_certified_tasksets > 0 and N_energy_blocking_cases > 0 and '
                                                        'N_processor_interference_cases > 0 and '
                                                        'N_complete_local_common_cases > 0',
                                           'required': True},
                  'processor_term_direct_scan_zero_mismatch': {'count_keys': ['N_instances', 'N_mismatches'],
                                                               'predicate': 'N_instances > 0 and N_mismatches == 0',
                                                               'required': True},
                  'schema_lineage_and_request_state_machine': {'count_keys': ['N_cycle_violations',
                                                                              'N_fk_violations',
                                                                              'N_rows',
                                                                              'N_state_transition_violations'],
                                                               'predicate': 'N_rows > 0 and N_fk_violations == 0 and '
                                                                            'N_state_transition_violations == 0 and '
                                                                            'N_cycle_violations == 0',
                                                               'required': True},
                  'service_curve_contract_checks': {'count_keys': ['N_curves', 'N_invalid'],
                                                    'predicate': 'N_curves > 0 and N_invalid == 0',
                                                    'required': True}},
 'CORE0B_gates': {'approved_build_identity_match': {'count_keys': ['N_unapproved_build_rows'],
                                                    'predicate': 'N_unapproved_build_rows == 0',
                                                    'required': True},
                  'deterministic_repeatability': {'count_keys': ['N_mismatches', 'N_repeated'],
                                                  'predicate': 'N_repeated > 0 and N_mismatches == 0',
                                                  'required': True},
                  'e0_parameterization_valid': {'count_keys': ['N_invalid_positive_cells', 'N_out_of_tolerance'],
                                                'predicate': 'N_invalid_positive_cells == 0 and N_out_of_tolerance == '
                                                             '0',
                                                'required': True},
                  'final_numeric_contract_identity': {'count_keys': ['N_mismatch'],
                                                      'predicate': 'N_mismatch == 0',
                                                      'required': True},
                  'foreign_key_lineage_gate': {'count_keys': ['N_ambiguous', 'N_broken', 'N_rows'],
                                               'predicate': 'N_rows > 0 and N_broken == 0 and N_ambiguous == 0',
                                               'required': True},
                  'formal_contract_hash_match': {'count_keys': ['N_mismatch'],
                                                 'predicate': 'N_mismatch == 0',
                                                 'required': True},
                  'formal_non_vacuity_gate': {'count_keys': ['N_formal_analysis_runs',
                                                             'N_formal_bound_audits',
                                                             'N_formal_generation_requests',
                                                             'N_positive_E0_planned_tracks',
                                                             'N_positive_E0_satisfied_traces'],
                                              'predicate': 'N_formal_generation_requests > 0 and '
                                                           'N_formal_analysis_runs > 0 and N_formal_bound_audits > 0 '
                                                           'and (N_positive_E0_planned_tracks == 0 or '
                                                           'N_positive_E0_satisfied_traces > 0)',
                                              'required': True},
                  'formal_soundness_observation_gate': {'count_keys': ['N_applicable_not_observed',
                                                                       'N_audited',
                                                                       'N_certified_bound_violations',
                                                                       'N_deadline_not_reached'],
                                                        'predicate': 'N_audited > 0 and N_certified_bound_violations '
                                                                     '== 0 and N_applicable_not_observed == 0 and '
                                                                     'N_deadline_not_reached == 0',
                                                        'required': True},
                  'generation_coverage_gate': {'count_keys': ['N_allowed_failure',
                                                              'N_cells_over_threshold',
                                                              'N_failure',
                                                              'N_requested',
                                                              'N_success'],
                                               'predicate': 'N_requested > 0 and N_failure <= N_allowed_failure and '
                                                            'N_cells_over_threshold == 0',
                                               'required': True},
                  'numeric_range_valid': {'count_keys': ['N_numeric_error', 'N_overflow_risk'],
                                          'predicate': 'N_overflow_risk == 0 and N_numeric_error == 0',
                                          'required': True},
                  'repeat_parameter_sensitive_exact_checks': {'count_keys': ['N_instances', 'N_mismatches'],
                                                              'predicate': 'N_instances > 0 and N_mismatches == 0',
                                                              'required': True},
                  'rho_e_parameterization_valid': {'count_keys': ['N_out_of_tolerance'],
                                                   'predicate': 'N_out_of_tolerance == 0',
                                                   'required': True},
                  'run_plan_accounting_gate': {'count_keys': ['N_accounted',
                                                              'N_illegal_transitions',
                                                              'N_planned',
                                                              'N_unaccounted'],
                                               'predicate': 'N_planned > 0 and N_planned == N_accounted and '
                                                            'N_unaccounted == 0 and N_illegal_transitions == 0',
                                               'required': True},
                  'service_curve_integerization_and_trace_validation': {'count_keys': ['N_invalid_curve',
                                                                                       'N_invalid_trace'],
                                                                        'predicate': 'N_invalid_curve == 0 and '
                                                                                     'N_invalid_trace == 0',
                                                                        'required': True}}}

_HEX64=re.compile(r'[0-9a-f]{64}\Z')
_SAFE_NAME=re.compile(r'[A-Za-z0-9_.+@-]+\Z')

def _is_hash(x): return isinstance(x,str) and bool(_HEX64.fullmatch(x))
def _safe_root_file(root:Path,name:str)->Path:
    if not isinstance(name,str) or not name or name in {'.','..'} or '/' in name or '\\' in name or Path(name).name!=name or unicodedata.normalize('NFC',name)!=name or any(ord(c)<32 for c in name):
        raise ValueError(f'unsafe artifact filename: {name!r}')
    p=root/name
    if p.is_symlink(): raise ValueError(f'symlink artifact forbidden: {name}')
    if not p.exists() or not p.is_file(): raise ValueError(f'missing artifact file: {name}')
    return p

def eval_predicate(expr:str,counts:dict)->bool:
    tree=ast.parse(expr,mode='eval')
    for node in ast.walk(tree):
        if not isinstance(node,ALLOWED_AST): raise ValueError(f'unsupported predicate node: {type(node).__name__}')
        if isinstance(node,ast.Name) and node.id not in counts: raise ValueError(f'unknown count in predicate: {node.id}')
    vals={}
    for k,v in counts.items():
        if isinstance(v,bool): raise ValueError(f'boolean gate count forbidden: {k}')
        if isinstance(v,int): vals[k]=v
        elif isinstance(v,str) and re.fullmatch(r'-?(?:0|[1-9][0-9]*)',v): vals[k]=int(v)
        else: raise ValueError(f'non-integer gate count: {k}={v!r}')
    return bool(eval(compile(tree,'<gate>','eval'),{'__builtins__':{}},vals))

def _validate_json_tree(x,path='root'):
    if x is None or isinstance(x,(str,bool,int)): return
    if isinstance(x,float): raise ValueError(f'JSON float forbidden at {path}')
    if isinstance(x,list):
        for i,v in enumerate(x): _validate_json_tree(v,f'{path}[{i}]')
        return
    if isinstance(x,dict):
        for k,v in x.items():
            if not isinstance(k,str): raise ValueError(f'non-string JSON key at {path}')
            _validate_json_tree(v,f'{path}.{k}')
        return
    raise ValueError(f'unsupported JSON type {type(x).__name__} at {path}')

def _load_json_strict(path:Path):
    text=read_text_strict(path)
    def hook(pairs):
        d={}
        for k,v in pairs:
            if k in d: raise ValueError(f'duplicate JSON key: {k}')
            d[k]=v
        return d
    obj=json.loads(text,object_pairs_hook=hook,parse_float=lambda x: (_ for _ in ()).throw(ValueError(f'JSON float forbidden: {x}')),parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f'JSON constant forbidden: {x}')))
    _validate_json_tree(obj,path.name)
    return obj

def _binding_for(formal,section,gid):
    gb=formal.get('gate_validator_bindings',{})
    if gb.get('replay_interface')!=REPLAY_INTERFACE: raise ValueError('formal gate replay interface mismatch')
    timeout=gb.get('replay_timeout_seconds')
    if not isinstance(timeout,int) or not 1<=timeout<=600: raise ValueError('formal replay_timeout_seconds must be integer in [1,600]')
    rec=gb.get(section,{}).get(gid)
    if not isinstance(rec,dict): raise ValueError(f'missing formal gate validator binding:{section}.{gid}')
    for f in ['validator_file','validator_version','validator_sha256']:
        if rec.get(f) in {None,''}: raise ValueError(f'unfilled formal gate validator binding:{section}.{gid}.{f}')
    if not _is_hash(rec.get('validator_sha256')): raise ValueError(f'invalid formal gate validator hash:{section}.{gid}')
    return rec,timeout

def _validate_evidence_bundle(bundle_path:Path,root:Path,section:str,gid:str,rec:dict,formal:dict,binding:dict):
    bundle=_load_json_strict(bundle_path)
    if set(bundle)!= {'evidence_bundle_metadata','predicate','counts','input_files','input_sha256','status'}:
        raise ValueError(f'gate evidence bundle top-level shape mismatch:{gid}')
    meta=bundle.get('evidence_bundle_metadata',{})
    expected_meta_keys={'version','gate_section','gate_id','plan_context_hash','formal_contract_hash','validator_file','validator_version','validator_sha256','replay_interface','evidence_bundle_hash'}
    if set(meta)!=expected_meta_keys: raise ValueError(f'gate evidence metadata shape mismatch:{gid}')
    if meta.get('version')!=VERSION or meta.get('gate_section')!=section or meta.get('gate_id')!=gid: raise ValueError(f'gate evidence identity mismatch:{gid}')
    if meta.get('plan_context_hash')!=formal.get('plan_context_contract',{}).get('plan_context_hash'): raise ValueError(f'gate evidence plan context mismatch:{gid}')
    if meta.get('formal_contract_hash')!=formal.get('contract_metadata',{}).get('formal_contract_hash'): raise ValueError(f'gate evidence formal contract mismatch:{gid}')
    if meta.get('replay_interface')!=REPLAY_INTERFACE: raise ValueError(f'gate evidence replay interface mismatch:{gid}')
    for f in ['validator_file','validator_version','validator_sha256']:
        expected=binding[{'validator_file':'validator_file','validator_version':'validator_version','validator_sha256':'validator_sha256'}[f]]
        if meta.get(f)!=expected: raise ValueError(f'gate evidence validator binding mismatch:{gid}:{f}')
    if meta.get('evidence_bundle_hash')!=canonical_object_self_hash(bundle,'evidence_bundle_metadata.evidence_bundle_hash',EVIDENCE_DOMAIN): raise ValueError(f'gate evidence self-hash mismatch:{gid}')
    if bundle.get('predicate')!=rec.get('predicate'): raise ValueError(f'gate evidence predicate mismatch:{gid}')
    if bundle.get('counts')!=rec.get('counts'): raise ValueError(f'gate evidence counts mismatch:{gid}')
    if bundle.get('status')!='PASSED': raise ValueError(f'gate evidence status not PASSED:{gid}')
    files=bundle.get('input_files'); hashes=bundle.get('input_sha256')
    if not isinstance(files,list) or not isinstance(hashes,list) or not files or len(files)!=len(hashes): raise ValueError(f'gate evidence input file/hash structure invalid:{gid}')
    if files!=rec.get('evidence_files',[])[1:] or hashes!=rec.get('evidence_sha256',[])[1:]: raise ValueError(f'gate evidence inputs differ from acceptance report:{gid}')
    if len(files)!=len(set(files)): raise ValueError(f'duplicate gate evidence input file:{gid}')
    if bundle_path.name in files: raise ValueError(f'gate evidence bundle cannot list itself as input:{gid}')
    for fn,h in zip(files,hashes):
        p=_safe_root_file(root,fn)
        if not _is_hash(h) or sha256_file(p)!=h: raise ValueError(f'gate evidence input hash mismatch:{gid}:{fn}')
    return bundle

def _replay_validator(root:Path,formal_path:Path,bundle_path:Path,section:str,gid:str,counts:dict,binding:dict,timeout:int):
    vp=_safe_root_file(root,binding['validator_file'])
    if sha256_file(vp)!=binding['validator_sha256']: raise ValueError(f'bound gate validator hash mismatch:{gid}')
    # A replay is read-only: protect the complete package file set and all bytes.
    before={p.name:sha256_file(p) for p in root.iterdir() if p.is_file() and not p.is_symlink()}
    nonregular_before=sorted(p.name for p in root.iterdir() if p.is_symlink() or not p.is_file())
    env={'PATH':os.environ.get('PATH',''),'PYTHONHASHSEED':'0','PYTHONIOENCODING':'utf-8','PYTHONDONTWRITEBYTECODE':'1'}
    cmd=[sys.executable,'-B',str(vp),'--replay-gate-evidence',bundle_path.name,'--formal-contract',formal_path.name]
    try:p=subprocess.run(cmd,cwd=root,env=env,capture_output=True,text=True,timeout=timeout,check=False)
    except subprocess.TimeoutExpired as e: raise ValueError(f'gate validator replay timeout:{gid}') from e
    after={p.name:sha256_file(p) for p in root.iterdir() if p.is_file() and not p.is_symlink()}
    nonregular_after=sorted(p.name for p in root.iterdir() if p.is_symlink() or not p.is_file())
    if before!=after or nonregular_before!=nonregular_after: raise ValueError(f'gate validator replay modified package:{gid}')
    if p.returncode!=0: raise ValueError(f'gate validator replay failed:{gid}:rc={p.returncode}:stderr={p.stderr[-500:]!r}')
    try:o=json.loads(p.stdout)
    except Exception as e: raise ValueError(f'gate validator replay output not JSON:{gid}') from e
    expected={'status':'PASSED','gate_section':section,'gate_id':gid,'counts':counts,'evidence_bundle_sha256':sha256_file(bundle_path),'formal_contract_hash':load_yaml_strict(formal_path).get('contract_metadata',{}).get('formal_contract_hash'),'validator_version':binding['validator_version']}
    if o!=expected: raise ValueError(f'gate validator replay output mismatch:{gid}')

def validate_report(report_path:Path,formal_path:Path|None=None,template_mode:bool=False)->list[str]:
    errors=[];root=report_path.parent
    try:report=load_yaml_strict(report_path)
    except Exception as e:return [f'acceptance report load:{e}']
    meta=report.get('acceptance_report_metadata',{})
    if meta.get('version')!=VERSION: errors.append('acceptance report version mismatch')
    if meta.get('canonical_serialization_file')!='ASAP_BLOCK_canonical_serialization_v1_3_10.yaml': errors.append('canonical serialization file mismatch')
    expected_hash=meta.get('acceptance_report_hash')
    if not template_mode:
        if not _is_hash(expected_hash): errors.append('acceptance_report_hash missing or invalid')
        else:
            actual=canonical_object_self_hash(report,'acceptance_report_metadata.acceptance_report_hash','ASAP_BLOCK:ACCEPTANCE_REPORT:v1.3.10')
            if actual!=expected_hash: errors.append('acceptance_report_hash mismatch')
        if meta.get('report_phase')!='CORE0B_FINAL': errors.append('executed report_phase must be CORE0B_FINAL')
        if formal_path is None: errors.append('formal contract is required for executed acceptance report')
    formal=None
    if formal_path:
        try:formal=load_yaml_strict(formal_path)
        except Exception as e: errors.append(f'formal contract load:{e}')
        if formal:
            fhash=formal.get('contract_metadata',{}).get('formal_contract_hash');pch=formal.get('plan_context_contract',{}).get('plan_context_hash')
            if not template_mode:
                if meta.get('formal_contract_hash')!=fhash: errors.append('acceptance report formal_contract_hash mismatch')
                if meta.get('plan_context_hash')!=pch: errors.append('acceptance report plan_context_hash mismatch')
            defs=formal.get('acceptance_gate_definitions',{})
            for phase,key in [('CORE0A','CORE0A_gates'),('CORE0B','CORE0B_gates')]:
                expected=set(defs.get(phase,[]));actual=set(report.get(key,{}))
                if expected!=actual: errors.append(f'gate set mismatch {phase}: missing={sorted(expected-actual)} extra={sorted(actual-expected)}')
    for section,specs in CANONICAL_GATE_SPECS.items():
        actual_gates=report.get(section,{})
        if set(actual_gates)!=set(specs): errors.append(f'canonical gate set mismatch {section}: missing={sorted(set(specs)-set(actual_gates))} extra={sorted(set(actual_gates)-set(specs))}')
        for gid,spec in specs.items():
            rec=actual_gates.get(gid,{})
            if rec.get('predicate')!=spec['predicate']: errors.append(f'gate predicate modified:{gid}')
            if rec.get('required') is not spec['required']: errors.append(f'gate required flag modified:{gid}')
            if sorted(rec.get('counts',{}))!=spec['count_keys']: errors.append(f'gate count structure modified:{gid}')
            if rec.get('replay_interface')!=REPLAY_INTERFACE: errors.append(f'gate replay interface modified:{gid}')
    ident=report.get('validator_identity',{});current=Path(__file__).resolve()
    if ident.get('acceptance_validator_file')!=current.name: errors.append('acceptance validator filename mismatch')
    if not template_mode:
        if ident.get('acceptance_validator_sha256')!=sha256_file(current): errors.append('acceptance validator sha256 mismatch')
        if not _is_hash(ident.get('validator_build_identity_hash')): errors.append('validator_build_identity_hash missing or invalid')
        if formal and formal.get('approved_builds',{}).get('approved_acceptance_validator_sha256')!=sha256_file(current): errors.append('formal approved acceptance-validator hash mismatch')
    failed=[]
    for section in ['CORE0A_gates','CORE0B_gates']:
        for gid,rec in report.get(section,{}).items():
            status=rec.get('status');required=rec.get('required',True)
            if template_mode:
                if status!='NOT_CHECKED': errors.append(f'template gate must be NOT_CHECKED:{gid}:{status}')
                for f in ['evidence_bundle_file','evidence_bundle_sha256','validator_name','validator_version','validator_sha256']:
                    if rec.get(f) not in {None,''}: errors.append(f'template gate field must be null:{gid}:{f}')
                if rec.get('evidence_files')!=[] or rec.get('evidence_sha256')!=[]: errors.append(f'template evidence lists must be empty:{gid}')
                continue
            if required and status!='PASSED': failed.append(gid)
            if not required and status not in {'PASSED','NOT_APPLICABLE_WITH_JUSTIFICATION'}: failed.append(gid)
            if status=='NOT_APPLICABLE_WITH_JUSTIFICATION':
                if required: errors.append(f'required gate cannot be not-applicable:{gid}')
                if not rec.get('notes'): errors.append(f'NA gate lacks justification:{gid}')
                continue
            if status!='PASSED': continue
            try:
                if not formal: raise ValueError('formal contract unavailable')
                binding,timeout=_binding_for(formal,section,gid)
                for f in ['validator_file','validator_version','validator_sha256']:
                    rf={'validator_file':'validator_name','validator_version':'validator_version','validator_sha256':'validator_sha256'}[f]
                    if rec.get(rf)!=binding[f]: raise ValueError(f'acceptance gate validator differs from formal binding:{gid}:{rf}')
                required_files=set(formal.get('output_contract',{}).get('required_files',[]))
                if binding['validator_file'] not in required_files: raise ValueError(f'gate validator not frozen in output required files:{gid}')
                files=rec.get('evidence_files',[]);hashes=rec.get('evidence_sha256',[])
                if not isinstance(files,list) or not isinstance(hashes,list) or len(files)<2 or len(files)!=len(hashes): raise ValueError(f'PASSED gate requires bundle plus at least one input evidence file:{gid}')
                if len(files)!=len(set(files)): raise ValueError(f'duplicate acceptance evidence file:{gid}')
                bundle_name=rec.get('evidence_bundle_file');bundle_hash=rec.get('evidence_bundle_sha256')
                if bundle_name!=files[0] or bundle_hash!=hashes[0]: raise ValueError(f'evidence bundle must be first evidence file:{gid}')
                bundle_path=_safe_root_file(root,bundle_name)
                if not _is_hash(bundle_hash) or sha256_file(bundle_path)!=bundle_hash: raise ValueError(f'evidence bundle hash mismatch:{gid}')
                for fn,h in zip(files,hashes):
                    p=_safe_root_file(root,fn)
                    if not _is_hash(h) or sha256_file(p)!=h: raise ValueError(f'evidence hash mismatch:{gid}:{fn}')
                _validate_evidence_bundle(bundle_path,root,section,gid,rec,formal,binding)
                if not eval_predicate(rec.get('predicate','False'),rec.get('counts',{})): raise ValueError(f'PASSED gate predicate false:{gid}')
                _replay_validator(root,formal_path,bundle_path,section,gid,rec.get('counts',{}),binding,timeout)
            except Exception as e: errors.append(str(e))
    if not template_mode:
        overall=report.get('overall_release_gate',{});declared=overall.get('status')
        derived='PASSED' if not failed and not errors else 'FAILED'
        if declared!=derived: errors.append(f'overall release gate mismatch: declared={declared} derived={derived}')
        if sorted(overall.get('failed_gate_ids',[]))!=sorted(failed): errors.append('failed_gate_ids mismatch')
        if formal:
            approved=formal.get('approved_builds',{});observed=report.get('approved_builds_observed',{})
            mapping={'approved_generator_build_identity_hash':'generator_build_identity_hash','approved_trace_generator_build_identity_hash':'trace_generator_build_identity_hash','approved_rta_build_identity_hash':'rta_build_identity_hash','approved_simulator_build_identity_hash':'simulator_build_identity_hash','approved_scheduler_build_identity_hash':'scheduler_build_identity_hash','approved_audit_build_identity_hash':'audit_build_identity_hash'}
            for fk,rk in mapping.items():
                if approved.get(fk)!=observed.get(rk): errors.append(f'approved build mismatch:{fk}')
    return errors

def _write_json(path,obj): path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
def self_test():
    cases={}
    spec=CANONICAL_GATE_SPECS['CORE0A_gates']['full_w_q_h_scan']
    cases['tautology_rejected']=spec['predicate']!='1 == 1'
    cases['required_flag_frozen']=spec['required'] is True
    cases['count_structure_nonempty']=bool(spec['count_keys'])
    cases['positive_E0_gate_frozen']='N_positive_E0_satisfied_traces > 0' in CANONICAL_GATE_SPECS['CORE0B_gates']['formal_non_vacuity_gate']['predicate']
    for bad,key in [('../outside.json','path_traversal_rejected'),('/tmp/x','absolute_path_rejected'),('a/b','subdirectory_rejected')]:
        try:_safe_root_file(Path('.'),bad);ok=False
        except:ok=True
        cases[key]=ok
    with tempfile.TemporaryDirectory() as td:
        root=Path(td);formal_hash='f'*64;plan_hash='e'*64
        validator=root/'gate_validator.py'
        validator.write_text(textwrap.dedent("""\
            import argparse,json,hashlib,pathlib,yaml
            p=argparse.ArgumentParser();p.add_argument('--replay-gate-evidence');p.add_argument('--formal-contract');a=p.parse_args()
            b=pathlib.Path(a.replay_gate_evidence);o=json.loads(b.read_text());f=yaml.safe_load(pathlib.Path(a.formal_contract).read_text())
            m=o['evidence_bundle_metadata']
            print(json.dumps({'status':'PASSED','gate_section':m['gate_section'],'gate_id':m['gate_id'],'counts':o['counts'],'evidence_bundle_sha256':hashlib.sha256(b.read_bytes()).hexdigest(),'formal_contract_hash':f['contract_metadata']['formal_contract_hash'],'validator_version':'T1'},sort_keys=True))
        """),encoding='utf-8')
        raw=root/'raw.json';_write_json(raw,{'records':[1]})
        binding={'validator_file':validator.name,'validator_version':'T1','validator_sha256':sha256_file(validator)}
        formal={'contract_metadata':{'formal_contract_hash':formal_hash},'plan_context_contract':{'plan_context_hash':plan_hash},'gate_validator_bindings':{'replay_interface':REPLAY_INTERFACE,'replay_timeout_seconds':10,'CORE0A_gates':{'full_w_q_h_scan':binding}},'output_contract':{'required_files':[validator.name]}}
        formal_path=root/'formal_contract.yaml';formal_path.write_text(__import__('yaml').safe_dump(formal,sort_keys=False),encoding='utf-8')
        counts={'N_scan_violations':0,'N_tasks_checked':1}
        bundle={'evidence_bundle_metadata':{'version':VERSION,'gate_section':'CORE0A_gates','gate_id':'full_w_q_h_scan','plan_context_hash':plan_hash,'formal_contract_hash':formal_hash,'validator_file':validator.name,'validator_version':'T1','validator_sha256':sha256_file(validator),'replay_interface':REPLAY_INTERFACE,'evidence_bundle_hash':None},'predicate':spec['predicate'],'counts':counts,'input_files':[raw.name],'input_sha256':[sha256_file(raw)],'status':'PASSED'}
        bundle['evidence_bundle_metadata']['evidence_bundle_hash']=canonical_object_self_hash(bundle,'evidence_bundle_metadata.evidence_bundle_hash',EVIDENCE_DOMAIN)
        bp=root/'bundle.json';_write_json(bp,bundle)
        rec={'predicate':spec['predicate'],'counts':counts,'evidence_files':[bp.name,raw.name],'evidence_sha256':[sha256_file(bp),sha256_file(raw)]}
        try:_validate_evidence_bundle(bp,root,'CORE0A_gates','full_w_q_h_scan',rec,formal,binding);_replay_validator(root,formal_path,bp,'CORE0A_gates','full_w_q_h_scan',counts,binding,10);cases['valid_structured_evidence_replays']=True
        except:cases['valid_structured_evidence_replays']=False
        bad=dict(rec);bad['counts']={'N_scan_violations':0,'N_tasks_checked':999}
        try:_validate_evidence_bundle(bp,root,'CORE0A_gates','full_w_q_h_scan',bad,formal,binding);cases['forged_counts_rejected']=False
        except:cases['forged_counts_rejected']=True
        badbinding=dict(binding);badbinding['validator_sha256']='0'*64
        try:_replay_validator(root,formal_path,bp,'CORE0A_gates','full_w_q_h_scan',counts,badbinding,10);cases['unbound_validator_rejected']=False
        except:cases['unbound_validator_rejected']=True
        mutator=root/'mutating_validator.py';mutator.write_text("from pathlib import Path\nPath('MUTATED.txt').write_text('x')\n",encoding='utf-8')
        mb={'validator_file':mutator.name,'validator_version':'T1','validator_sha256':sha256_file(mutator)}
        try:_replay_validator(root,formal_path,bp,'CORE0A_gates','full_w_q_h_scan',counts,mb,10);cases['mutating_replay_rejected']=False
        except:cases['mutating_replay_rejected']=True
        (root/'MUTATED.txt').unlink(missing_ok=True)
    return {'status':'PASSED' if all(cases.values()) else 'FAILED','cases':cases}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('report',nargs='?');ap.add_argument('--formal-contract');ap.add_argument('--template-mode',action='store_true');ap.add_argument('--self-test',action='store_true');args=ap.parse_args()
    if args.self_test:
        cases=self_test();out={'status':cases['status'],'validator_version':VERSION,'profile':'SELF_TEST_ONLY','cases':cases['cases']}
        print(json.dumps(out,ensure_ascii=False,indent=2));return 0 if out['status']=='PASSED' else 1
    if not args.report: ap.error('report is required unless --self-test is used')
    try:errors=validate_report(Path(args.report),Path(args.formal_contract) if args.formal_contract else None,args.template_mode)
    except Exception as e:errors=[f'validator exception:{e}']
    out={'status':'PASSED' if not errors else 'FAILED','validator_version':VERSION,'scope':'acceptance report self-hash; immutable gates; formal-bound validator replay over self-hashed structured evidence; safe file paths; evidence and build identity; mechanically derived release gate','errors':errors}
    print(json.dumps(out,ensure_ascii=False,indent=2));return 0 if not errors else 1
if __name__=='__main__':sys.exit(main())
