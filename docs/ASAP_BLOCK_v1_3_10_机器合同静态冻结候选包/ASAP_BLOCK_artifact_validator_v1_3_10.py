#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import argparse, hashlib, json, re, shutil, subprocess, sys, tempfile, yaml
sys.dont_write_bytecode=True
from ASAP_BLOCK_validation_common_v1_3_10 import *
VERSION='1.3.10'
ALGORITHM='SHA256_DOMAIN_SEPARATED_CANONICAL_V1_3_10'
SEMANTIC_FIELD_TYPES={
 ('run_plan_definition.csv','base_generation_cell_id'):('identifier',None),
 ('run_plan_definition.csv','base_generation_request_id'):('identifier',None),
 ('generation_requests.csv','generation_request_id'):('identifier',None),
 ('generation_requests.csv','base_generation_cell_id'):('identifier',None),
 ('generation_requests.csv','generation_failure_reason'):('string',None),
 ('paired_transformations.csv','base_generation_cell_id'):('identifier',None),
 ('per_taskset_results.csv','generation_request_id'):('identifier',None),
 ('task_definitions.csv','P_rounding_mode'):('enum','rounding_direction'),
 ('per_taskset_results.csv','rho_e_tolerance_mode'):('enum','rho_e_tolerance_mode'),
 ('per_taskset_results.csv','power_latent_mapping_version'):('identifier',None),
 ('per_taskset_results.csv','energy_demand_rounding'):('enum','rounding_direction'),
 ('per_taskset_results.csv','energy_supply_rounding'):('enum','rounding_direction'),
 ('per_taskset_results.csv','numeric_integer_type'):('enum','numeric_integer_type'),
 ('per_task_results.csv','P_hat_i_rounding'):('enum','rounding_direction'),
 ('rta_dependency_records.csv','theta_source_mode'):('enum','theta_source_mode'),
 ('simulation_taskset_summary.csv','energy_account_semantics_version'):('identifier',None),
 ('simulation_taskset_summary.csv','battery_mode'):('enum','battery_mode'),
 ('analysis_simulation_compatibility_checks.csv','energy_account_match_status'):('enum','match_status'),
 ('analysis_simulation_compatibility_checks.csv','power_upper_bound_status'):('enum','match_status'),
}
FILES={
'markdown':'ASAP_BLOCK_实验配置与验收规范_v1_3_10_最终机器合同与验证闭合版.md',
'schema':'ASAP_BLOCK_experiment_schema_v1_3_10.yaml','dictionary':'ASAP_BLOCK_data_dictionary_v1_3_10.yaml',
'canonical':'ASAP_BLOCK_canonical_serialization_v1_3_10.yaml','interface_manifest':'ASAP_BLOCK_machine_interface_manifest_v1_3_10.yaml',
'formal':'ASAP_BLOCK_formal_contract_template_v1_3_10.yaml','generator':'ASAP_BLOCK_generator_contract_template_v1_3_10.yaml',
'simulation':'ASAP_BLOCK_simulation_contract_template_v1_3_10.yaml','trace':'ASAP_BLOCK_trace_generator_contract_template_v1_3_10.yaml',
'acceptance':'ASAP_BLOCK_acceptance_report_template_v1_3_10.yaml','common':'ASAP_BLOCK_validation_common_v1_3_10.py',
'artifact_validator':'ASAP_BLOCK_artifact_validator_v1_3_10.py','result_validator':'ASAP_BLOCK_result_validator_v1_3_10.py',
'acceptance_validator':'ASAP_BLOCK_acceptance_report_validator_v1_3_10.py','plan_template':'ASAP_BLOCK_run_plan_definition_template_v1_3_10.csv',
'plan_dependencies_template':'ASAP_BLOCK_run_plan_dependencies_template_v1_3_10.csv','execution_log_template':'ASAP_BLOCK_run_execution_log_template_v1_3_10.csv',
'validation_summary':'ASAP_BLOCK_v1_3_10_验证摘要.txt'}
REPORT='validation_report.json'
class V:
    def __init__(self): self.e=[]; self.c={}
    def err(self,x): self.e.append(x)
    def mark(self,k,ok): self.c[k]='PASSED' if ok else 'FAILED'

def projection(s):
    t=s['tables']; e=s['enums']; m=s['failure_masks']
    iface={n:{'pk':d['primary_key'],'required':d['required'],'conditional':d['conditionally_required'],'optional':d['optional_diagnostic'],'unique':d.get('unique_constraints',[]),'fks':d.get('foreign_keys',{}),'cfks':d.get('composite_foreign_keys',[])} for n,d in t.items()}
    return {'version':VERSION,'canonical_algorithm':ALGORITHM,'run_plan_hash_name':'run_plan_bundle_hash','formal_grid_hash_name':'formal_grid_hash','analysis_energy_unit_field':'analysis_energy_unit_hash','table_names':list(t),'enum_names':sorted(e),'failure_mask_names':sorted(m),'table_interface_digest':hashlib.sha256(canonical_json_bytes(iface)).hexdigest(),'enum_digest':hashlib.sha256(canonical_json_bytes(e)).hexdigest(),'failure_mask_digest':hashlib.sha256(canonical_json_bytes(m)).hexdigest(),'required_markdown_headings':['# 0. 最终审计结论','# 4. CORE-0A、pilot、CORE-0B 两级验收','# 16. 结果文件、机器接口与复现契约','# 23. v1.3.10 机器合同闭合修订'],'critical_fields':{'run_plan_definition.csv':['request_id','request_payload_hash','expected_output_id','plan_context_hash'],'run_execution_log.csv':['request_id','run_phase','plan_context_hash','formal_contract_hash','execution_status','actual_output_id'],'per_taskset_results.csv':['analysis_run_id','run_phase','plan_context_hash','formal_contract_hash','analysis_energy_unit_hash','analysis_certification_status','dominance_invariant_status','dominance_violation_count'],'simulation_bound_checks.csv':['request_id','bound_audit_run_id','simulation_run_id','analysis_run_id','task_id','task_solver_status','task_certification_status','applicability_failure_mask','applicability_pending_mask','soundness_check_status']},'validation_claim':'machine-interface manifest conformance only; full natural-language semantic equivalence is not mechanically claimed'}

def result(v):
    return {'status':'PASSED' if not v.e else 'FAILED','validator_version':VERSION,'scope':'artifact file set/sidecars; strict YAML; embedded bindings; schema/dictionary/conditions/keys; canonical preimages; machine-interface manifest; Markdown structure; templates. Runtime theorem correctness is not claimed.','checks':v.c,'errors':v.e}

def validate(root:Path,check_report=True,run_subvalidator_checks=True):
    v=V(); p={k:root/f for k,f in FILES.items()}
    exp=set(FILES.values())|{REPORT}|{f+'.sha256' for f in FILES.values()}|{REPORT+'.sha256'}
    bad_entries=[x.name for x in root.iterdir() if x.is_symlink() or not x.is_file()]
    if bad_entries:v.err(f'non-regular artifact entries forbidden:{sorted(bad_entries)}')
    act={x.name for x in root.iterdir() if x.is_file() and not x.is_symlink()}
    if act-exp:v.err(f'undeclared extra files:{sorted(act-exp)}')
    if exp-act:v.err(f'missing artifact files:{sorted(exp-act)}')
    v.mark('exact_artifact_file_set',act==exp)
    if exp-act:return result(v)
    for x in list(p.values())+[root/REPORT]:
        try:read_text_strict(x);validate_sidecar(x)
        except Exception as z:v.err(str(z))
    v.mark('sidecar_and_text_integrity',not any('sidecar' in x or 'newline' in x or 'BOM' in x for x in v.e))
    try:
        s=load_yaml_strict(p['schema']);d=load_yaml_strict(p['dictionary']);c=load_yaml_strict(p['canonical']);mi=load_yaml_strict(p['interface_manifest']);f=load_yaml_strict(p['formal']);a=load_yaml_strict(p['acceptance'])
        children={k:load_yaml_strict(p[k]) for k in ['generator','simulation','trace']}
    except Exception as z:
        v.err(f'YAML load:{z}');v.mark('strict_yaml',False);return result(v)
    v.mark('strict_yaml',True)
    # Schema authority references must resolve to the exact files in this artifact version.
    sm=s.get('schema_metadata',{})
    expected_schema_refs={
        'version':VERSION,
        'markdown_companion':FILES['markdown'],
        'data_dictionary_file':FILES['dictionary'],
        'canonical_serialization_file':FILES['canonical'],
        'machine_interface_manifest_file':FILES['interface_manifest'],
        'artifact_validator_file':FILES['artifact_validator'],
        'result_validator_file':FILES['result_validator'],
        'acceptance_validator_file':FILES['acceptance_validator'],
    }
    for fld,expected in expected_schema_refs.items():
        if str(sm.get(fld))!=str(expected):
            v.err(f'schema authority reference mismatch:{fld}:{sm.get(fld)!r}!={expected!r}')
    v.mark('schema_authority_references',not any(x.startswith('schema authority reference mismatch:') for x in v.e))
    # Formal embedded artifact bindings (formal and human summary intentionally excluded).
    b=f.get('artifact_bindings',{})
    for k,x in p.items():
        if k in {'formal','validation_summary'}:continue
        r=b.get(k)
        if not isinstance(r,dict):v.err(f'missing formal artifact binding:{k}');continue
        if r.get('file')!=x.name:v.err(f'embedded filename mismatch:{k}')
        if r.get('sha256')!=sha256_file(x):v.err(f'embedded hash mismatch:{k}')
    v.mark('embedded_hash_binding',not any('embedded' in x or 'artifact binding' in x for x in v.e))
    st=s.get('tables',{});dt=d.get('tables',{})
    if set(st)!=set(dt):v.err('schema/dictionary table set mismatch')
    for n,td in st.items():
        cl={}
        for q in ['required','conditionally_required','optional_diagnostic']:
            for fld in td.get(q,[]):
                if fld in cl:v.err(f'field classified twice:{n}.{fld}')
                cl[fld]=q
        if td.get('canonical_column_order')!=list(cl):v.err(f'canonical column order mismatch:{n}')
        dfs=dt.get(n,{}).get('fields',{})
        if set(cl)!=set(dfs):v.err(f'schema/dictionary fields mismatch:{n}')
        cov={fld for r in td.get('conditional_rules',[]) for fld in r.get('then_required',[])}
        missing=set(td.get('conditionally_required',[]))-cov
        if missing:v.err(f'conditional field lacks rule:{n}:{sorted(missing)}')
        for fld,sp in dfs.items():
            if sp.get('field_class')!=cl.get(fld):v.err(f'field class mismatch:{n}.{fld}')
            if sp.get('type')=='enum' and sp.get('enum_ref') not in s.get('enums',{}):v.err(f'bad enum ref:{n}.{fld}')
            if sp.get('type')=='enum_set' and sp.get('enum_ref') not in s.get('failure_masks',{}):v.err(f'bad mask ref:{n}.{fld}')
            expected=SEMANTIC_FIELD_TYPES.get((n,fld))
            if expected and (sp.get('type'),sp.get('enum_ref'))!=expected:v.err(f'semantic type mismatch:{n}.{fld}:{(sp.get("type"),sp.get("enum_ref"))}!={expected}')
    v.mark('schema_dictionary_and_conditions',not any(x.startswith(('schema/','field ','canonical ','conditional ','bad enum','bad mask','semantic type')) for x in v.e))
    for n,td in st.items():
        cols=set(td.get('canonical_column_order',[]))
        if not set(td.get('primary_key',[]))<=cols:v.err(f'PK missing columns:{n}')
        for u in td.get('unique_constraints',[]):
            if not isinstance(u,list) or not set(u)<=cols:v.err(f'invalid unique constraint:{n}:{u}')
        for lf,r in td.get('foreign_keys',{}).items():
            rt,rf=r.rsplit('.',1)
            if lf not in cols or rt not in st or rf not in st[rt]['canonical_column_order']:v.err(f'invalid FK:{n}.{lf}->{r}')
        for fk in td.get('composite_foreign_keys',[]):
            lo=fk['local'];rt=fk['references']['table'];rc=fk['references']['columns']
            if not set(lo)<=cols or rt not in st or not set(rc)<=set(st[rt]['canonical_column_order']):v.err(f'invalid composite FK:{n}:{lo}->{rt}:{rc}');continue
            if rc not in [st[rt]['primary_key']]+st[rt].get('unique_constraints',[]):v.err(f'composite FK target not unique:{n}->{rt}:{rc}')
    v.mark('key_and_lineage_structure',not any('FK' in x or 'PK' in x or 'unique constraint' in x for x in v.e))
    pre=c.get('preimages',{});need={'plan_context_hash','formal_grid_hash','seed_derivation_context_hash','formal_seed_set_hash','run_plan_bundle_hash','formal_contract_hash','request_payload_hash','request_id','expected_output_id','request_output_bundle_hash','carry_in_vector_hash','dependency_record_hash','task_result_hash','formal_primary_selector','build_identity_hash'}
    if not need<=set(pre):v.err(f'missing canonical preimages:{sorted(need-set(pre))}')
    if c.get('canonical_serialization_metadata',{}).get('id_algorithm')!=ALGORITHM:v.err('canonical algorithm mismatch')
    payload=c.get('request_type_payload_fields',{});reqtypes=set(s.get('enums',{}).get('request_type',[]))
    if set(payload)!=reqtypes:v.err('request payload type set mismatch')
    plan_cols=set(st['run_plan_definition.csv']['canonical_column_order'])
    for typ,fields in payload.items():
        if not set(fields)<=plan_cols:v.err(f'request payload fields absent from plan schema:{typ}:{sorted(set(fields)-plan_cols)}')
    outcomp=c.get('output_bundle_composition',{})
    if set(outcomp)!=reqtypes:v.err('output bundle type set mismatch')
    v.mark('canonical_preimage_and_request_coverage',not any('canonical' in x or 'request payload' in x or 'output bundle' in x for x in v.e))
    if mi.get('projection')!=projection(s):v.err('machine interface manifest projection mismatch')
    txt=read_text_strict(p['markdown']);m=re.search(r'ASAP_BLOCK_MACHINE_INTERFACE_MANIFEST_SHA256: ([0-9a-f]{64})',txt)
    if not m or m.group(1)!=sha256_file(p['interface_manifest']):v.err('Markdown interface manifest hash marker mismatch')
    for h in projection(s)['required_markdown_headings']:
        if h not in txt:v.err(f'Markdown required heading missing:{h}')
    if 'full natural-language semantic equivalence is not mechanically claimed' not in txt:v.err('Markdown validation claim boundary missing')
    expected_authority=f'MACHINE_INTERFACE_AUTHORITY_V{VERSION.replace(".","_")}'
    authority_markers=re.findall(r'MACHINE_INTERFACE_AUTHORITY_V[0-9_]+',txt)
    if authority_markers!=[expected_authority]:
        v.err(f'Markdown authority marker mismatch:{authority_markers!r}!={[expected_authority]!r}')
    if txt.count('```')%2:v.err('Markdown code-fence structure damaged')
    if txt.count('$$')%2:v.err('Markdown display-math delimiter structure damaged')
    if len(re.findall(r'^#(?:#| )',txt,flags=re.M))<20:v.err('Markdown section structure unexpectedly sparse')
    v.mark('machine_interface_manifest_and_markdown_structure',not any('manifest' in x.lower() or 'Markdown' in x for x in v.e))
    if f.get('run_plan_contract',{}).get('run_plan_hash_field')!='run_plan_bundle_hash':v.err('formal run-plan hash name mismatch')
    th=f.get('theory_contract',{})
    if th.get('rta_formula_version')!='v9.3':v.err('formal theory baseline is not v9.3')
    if th.get('theory_document_sha256')!='524d4f84b04185609735a2be3ff54984149be1478a111044494ec1f8ff65098e':v.err('formal v9.3 theory hash mismatch')
    if th.get('fixed_carry_in_corollary_version')!='V9_3_SECTION_9_5_FIXED_CARRY_IN_INTERFACE':v.err('fixed-carry-in interface version mismatch')
    if th.get('fixed_carry_in_corollary_sha256')!='524d4f84b04185609735a2be3ff54984149be1478a111044494ec1f8ff65098e':v.err('fixed-carry-in interface hash mismatch')
    if 'TASK_LEVEL_AUXILIARY' in s.get('enums',{}).get('analysis_method_role',[]):v.err('legacy task-level analysis role remains enabled')
    if 'TASK_LEVEL_CERTIFIED_ONLY' in s.get('enums',{}).get('analysis_certification_status',[]):v.err('legacy task-level certification remains enabled')
    if s.get('enums',{}).get('fixed_carry_in_corollary_status')!=['ACTIVE','HASH_MISMATCH','NOT_APPLICABLE']:v.err('fixed-carry-in interface status enum mismatch')
    expected_certification_semantics={
        'method_roles':{'CW-Theta^cw':'MAIN_METHOD','LOC-Theta^loc':'MAIN_METHOD','CW-D':'AUXILIARY_ABLATION','LOC-D':'AUXILIARY_ABLATION','LOC-Theta^cw':'AUXILIARY_ABLATION'},
        'taskset_proven_equivalence':'analysis_certification_status == CERTIFIED_TASKSET',
        'pre_joint_success_task_status':'PROVISIONAL_NOT_CERTIFIED',
        'loc_theta_cw':{'source_variant':'CW-Theta^cw','source_solver_status':'COMPLETED','source_certification_status':'CERTIFIED_TASKSET','fixed_carry_in_corollary_status':'ACTIVE','dependency_vector_check_status':'VALID','complete_compatible_vector_status':'CERTIFIED_TASKSET','invalid_dependency_formal_status':'NOT_APPLICABLE','valid_domain_no_candidate_status':'INTERNAL_CONFORMANCE_FAILURE','valid_domain_no_candidate_dominance_status':'DOMINANCE_INVARIANT_VIOLATION'},
    }
    if f.get('analysis_contract',{}).get('certification_semantics')!=expected_certification_semantics:v.err('formal certification semantics mismatch')
    if 'formal_grid_hash' not in f.get('formal_grid_contract',{}):v.err('formal_grid_hash missing')
    approved=['approved_generator_build_identity_hash','approved_trace_generator_build_identity_hash','approved_rta_build_identity_hash','approved_simulator_build_identity_hash','approved_scheduler_build_identity_hash','approved_audit_build_identity_hash','approved_artifact_validator_sha256','approved_result_validator_sha256','approved_acceptance_validator_sha256','approved_validation_common_sha256']
    for fld in approved:
        if fld not in f.get('approved_builds',{}):v.err(f'approved build/hash field missing:{fld}')
    if f.get('seed_contract',{}).get('seed_derivation_algorithm')!=ALGORITHM:v.err('formal seed algorithm mismatch')
    for k,obj in children.items():
        meta=obj.get('contract_metadata',{})
        if meta.get('version')!=VERSION:v.err(f'child contract version mismatch:{k}')
    for ph,key in [('CORE0A','CORE0A_gates'),('CORE0B','CORE0B_gates')]:
        if set(f.get('acceptance_gate_definitions',{}).get(ph,[]))!=set(a.get(key,{})):v.err(f'acceptance gate set mismatch:{ph}')
    gb=f.get('gate_validator_bindings',{})
    if gb.get('replay_interface')!='ASAP_BLOCK_GATE_REPLAY_V1':v.err('formal gate replay interface mismatch')
    if not isinstance(gb.get('replay_timeout_seconds'),int) or not 1<=gb.get('replay_timeout_seconds')<=600:v.err('formal gate replay timeout invalid')
    for ph,key in [('CORE0A','CORE0A_gates'),('CORE0B','CORE0B_gates')]:
        expected=set(f.get('acceptance_gate_definitions',{}).get(ph,[]));bindings=gb.get(key,{})
        if set(bindings)!=expected:v.err(f'formal gate validator binding set mismatch:{ph}')
        for gid,rec in bindings.items():
            if set(rec)!={'validator_file','validator_version','validator_sha256'}:v.err(f'gate validator binding shape mismatch:{gid}')
            if any(rec.get(x) is not None for x in rec):v.err(f'formal template gate validator binding must be null:{gid}')
    for key in ['CORE0A_gates','CORE0B_gates']:
        for gid,rec in a.get(key,{}).items():
            if rec.get('replay_interface')!='ASAP_BLOCK_GATE_REPLAY_V1':v.err(f'acceptance template replay interface mismatch:{gid}')
            if rec.get('evidence_bundle_file') is not None or rec.get('evidence_bundle_sha256') is not None:v.err(f'acceptance template evidence bundle fields must be null:{gid}')
    req=set(f.get('output_contract',{}).get('required_files',[]));er={'config.yaml','generator_contract.yaml','simulation_contract.yaml','trace_generator_contract.yaml','formal_contract.yaml','acceptance_report.yaml','manifest.json','git_commit.txt','git_status.txt','sha256sum.txt','ASAP_BLOCK_validation_common_v1_3_10.py','ASAP_BLOCK_artifact_validator_v1_3_10.py','ASAP_BLOCK_result_validator_v1_3_10.py','ASAP_BLOCK_acceptance_report_validator_v1_3_10.py','ASAP_BLOCK_formal_contract_template_v1_3_10.yaml'}|set(st)|{rec.get('file') for rec in f.get('artifact_bindings',{}).values() if isinstance(rec,dict)}
    if not er<=req:v.err(f'formal output contract missing:{sorted(er-req)}')
    # CSV templates are exact schema headers.
    for key,tn in [('plan_template','run_plan_definition.csv'),('plan_dependencies_template','run_plan_dependencies.csv'),('execution_log_template','run_execution_log.csv')]:
        h,_=read_csv_strict(p[key])
        if h!=st[tn]['canonical_column_order']:v.err(f'CSV template header mismatch:{tn}')
    v.mark('formal_and_template_structure',not any(x.startswith(('formal ','approved ','child ','acceptance ','CSV template','gate validator')) or 'output contract' in x or 'seed algorithm' in x or 'gate replay' in x for x in v.e))
    validator_ok=True
    if run_subvalidator_checks:
        validator_commands=[
            ([sys.executable,str(p['acceptance_validator']),'--self-test'],'acceptance validator self-test'),
            ([sys.executable,str(p['result_validator']),'--self-test'],'result validator self-test'),
            ([sys.executable,str(p['result_validator']),str(root),'--schema-only'],'result validator schema-only'),
            ([sys.executable,str(p['acceptance_validator']),str(p['acceptance']),'--formal-contract',str(p['formal']),'--template-mode'],'acceptance template validation')]
        for cmd,label in validator_commands:
            try:
                z=subprocess.run(cmd,cwd=root,capture_output=True,text=True,timeout=120,check=False,env={**__import__('os').environ,'PYTHONDONTWRITEBYTECODE':'1'})
                if z.returncode!=0:v.err(f'{label} failed:rc={z.returncode}:stdout={z.stdout[-500:]!r}:stderr={z.stderr[-500:]!r}');validator_ok=False
            except Exception as z:v.err(f'{label} exception:{z}');validator_ok=False
    v.mark('validator_self_tests_schema_and_template',validator_ok)
    fresh=result(v)
    if check_report:
        try:
            stored=json.loads(read_text_strict(root/REPORT))
            for k in ['status','validator_version','scope','checks','errors']:
                if stored.get(k)!=fresh.get(k):v.err(f'stored validation_report mismatch:{k}')
        except Exception as z:v.err(f'validation_report invalid:{z}')
        v.mark('stored_validation_report_matches_fresh_validation',not any('validation_report' in x for x in v.e))
    return result(v)

def cp(root,dst):
    for x in root.iterdir():
        if x.is_file():shutil.copy2(x,dst/x.name)
def self_test(root):
    base=validate(root);cases={'baseline_valid_before_mutation':base['status']=='PASSED'}
    if not cases['baseline_valid_before_mutation']:return {'status':'FAILED','cases':cases,'baseline_errors':base['errors']}
    def mutate(fn):
        with tempfile.TemporaryDirectory() as q:
            d=Path(q);cp(root,d);fn(d);return validate(d,check_report=False,run_subvalidator_checks=False)['status']=='FAILED'
    def bind(d,key):
        f=d/FILES['formal'];y=load_yaml_strict(f);y['artifact_bindings'][key]['sha256']=sha256_file(d/FILES[key]);f.write_text(yaml.safe_dump(y,sort_keys=False,allow_unicode=True),encoding='utf-8');write_sidecar(f)
    cases['wrong_embedded_hash_rejected']=mutate(lambda d:(lambda f,y:(y['artifact_bindings']['schema'].__setitem__('sha256','0'*64),f.write_text(yaml.safe_dump(y,sort_keys=False,allow_unicode=True),encoding='utf-8'),write_sidecar(f)))(d/FILES['formal'],load_yaml_strict(d/FILES['formal'])))
    cases['duplicate_yaml_key_rejected']=mutate(lambda d:(lambda x:(x.write_text(read_text_strict(x)+'\nschema_metadata:\n  version: 9\n',encoding='utf-8'),write_sidecar(x)))(d/FILES['schema']))
    cases['yaml_alias_rejected']=mutate(lambda d:(lambda x:(x.write_text('a: &x 1\nb: *x\n',encoding='utf-8'),write_sidecar(x),bind(d,'generator')))(d/FILES['generator']))
    cases['implicit_float_rejected']=mutate(lambda d:(lambda x:(x.write_text('x: 1.25\n',encoding='utf-8'),write_sidecar(x),bind(d,'generator')))(d/FILES['generator']))
    cases['wrong_sidecar_rejected']=mutate(lambda d:(d/(FILES['schema']+'.sha256')).write_text('0'*64+'  '+FILES['schema']+'\n',encoding='utf-8'))
    def stale_theory_hash(d):
        x=d/FILES['formal'];y=load_yaml_strict(x);y['theory_contract']['theory_document_sha256']='0'*64
        x.write_text(yaml.safe_dump(y,sort_keys=False,allow_unicode=True),encoding='utf-8');write_sidecar(x)
    cases['stale_v9_2_or_wrong_theory_hash_rejected']=mutate(stale_theory_hash)
    def legacy_cert_enum(d):
        x=d/FILES['schema'];y=load_yaml_strict(x);y['enums']['analysis_certification_status'].append('TASK_LEVEL_CERTIFIED_ONLY')
        x.write_text(yaml.safe_dump(y,sort_keys=False,allow_unicode=True),encoding='utf-8');write_sidecar(x);bind(d,'schema')
    cases['legacy_task_level_certification_enum_rejected']=mutate(legacy_cert_enum)
    def fake_md(d):
        x=d/FILES['markdown'];x.write_text('# fake\n<!-- ASAP_BLOCK_MACHINE_INTERFACE_MANIFEST_SHA256: '+sha256_file(d/FILES['interface_manifest'])+' -->\n',encoding='utf-8');write_sidecar(x);bind(d,'markdown')
    cases['fake_markdown_rejected']=mutate(fake_md)
    def bad_schema_authority_refs(d):
        x=d/FILES['schema'];y=load_yaml_strict(x);sm=y['schema_metadata']
        sm['markdown_companion']='ASAP_BLOCK_实验配置与验收规范_v1_3_7_机器合同闭合与验证器强化版.md'
        sm['artifact_validator_file']='ASAP_BLOCK_artifact_validator_v1_3_7.py'
        sm['result_validator_file']='ASAP_BLOCK_result_validator_v1_3_7.py'
        x.write_text(yaml.safe_dump(y,sort_keys=False,allow_unicode=True),encoding='utf-8');write_sidecar(x);bind(d,'schema')
    cases['stale_schema_authority_references_rejected']=mutate(bad_schema_authority_refs)
    def bad_markdown_authority(d):
        x=d/FILES['markdown'];t=read_text_strict(x).replace('MACHINE_INTERFACE_AUTHORITY_V1_3_10','MACHINE_INTERFACE_AUTHORITY_V1_3_7',1)
        x.write_text(t,encoding='utf-8');write_sidecar(x);bind(d,'markdown')
    cases['stale_markdown_authority_marker_rejected']=mutate(bad_markdown_authority)
    cases['undeclared_extra_file_rejected']=mutate(lambda d:(d/'EXTRA.txt').write_text('x\n',encoding='utf-8'))
    cases['extra_directory_rejected']=mutate(lambda d:(d/'EXTRA_DIR').mkdir())
    def symlink_attack(d):
        target=d/FILES['schema'];link=d/'LINK.yaml';link.symlink_to(target.name)
    cases['symlink_rejected']=mutate(symlink_attack)
    def bad_header(d):
        x=d/FILES['plan_template'];x.write_text('request_id\n',encoding='utf-8');write_sidecar(x);bind(d,'plan_template')
    cases['bad_template_header_rejected']=mutate(bad_header)
    with tempfile.TemporaryDirectory() as q:
        d=Path(q);cp(root,d);o=validate(d,check_report=False,run_subvalidator_checks=False);atomic_write_json_with_sidecar(d/REPORT,o);cases['report_write_keeps_package_valid']=validate(d,run_subvalidator_checks=False)['status']=='PASSED'
    return {'status':'PASSED' if all(cases.values()) else 'FAILED','cases':cases}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('root',nargs='?',default='.');ap.add_argument('--write-report',action='store_true');ap.add_argument('--self-test',action='store_true');a=ap.parse_args();root=Path(a.root).resolve()
    if a.write_report:
        first=validate(root,check_report=False);atomic_write_json_with_sidecar(root/REPORT,first);o=validate(root,check_report=True)
    else:o=validate(root,check_report=True)
    if a.self_test:
        o['self_test']=self_test(root)
        if o['self_test']['status']!='PASSED':o['status']='FAILED'
    print(json.dumps(o,ensure_ascii=False,indent=2));return 0 if o['status']=='PASSED' else 1
if __name__=='__main__':sys.exit(main())
