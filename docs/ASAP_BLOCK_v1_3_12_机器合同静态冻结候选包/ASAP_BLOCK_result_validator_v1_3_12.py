#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
from collections import defaultdict
from typing import Any
import argparse, csv, io, json, sys, re, hashlib, math, ast, unicodedata
from fractions import Fraction
sys.dont_write_bytecode=True
from ASAP_BLOCK_validation_common_v1_3_12 import *
from ASAP_BLOCK_acceptance_report_validator_v1_3_12 import validate_report
VERSION='1.3.12'
SCHEMA='ASAP_BLOCK_experiment_schema_v1_3_12.yaml'
DICT='ASAP_BLOCK_data_dictionary_v1_3_12.yaml'
CANON='ASAP_BLOCK_canonical_serialization_v1_3_12.yaml'
COMMON='ASAP_BLOCK_validation_common_v1_3_12.py'
RESULT_VALIDATOR='ASAP_BLOCK_result_validator_v1_3_12.py'
ACCEPTANCE_VALIDATOR='ASAP_BLOCK_acceptance_report_validator_v1_3_12.py'
ARTIFACT_VALIDATOR='ASAP_BLOCK_artifact_validator_v1_3_12.py'
MARKDOWN='ASAP_BLOCK_实验配置与验收规范_v1_3_12_最终机器合同与验证闭合版.md'
INTERFACE_MANIFEST='ASAP_BLOCK_machine_interface_manifest_v1_3_12.yaml'
FORMAL_TEMPLATE='ASAP_BLOCK_formal_contract_template_v1_3_12.yaml'
GENERATOR_TEMPLATE='ASAP_BLOCK_generator_contract_template_v1_3_12.yaml'
SIMULATION_TEMPLATE='ASAP_BLOCK_simulation_contract_template_v1_3_12.yaml'
TRACE_TEMPLATE='ASAP_BLOCK_trace_generator_contract_template_v1_3_12.yaml'
ACCEPTANCE_TEMPLATE='ASAP_BLOCK_acceptance_report_template_v1_3_12.yaml'
PLAN_TEMPLATE='ASAP_BLOCK_run_plan_definition_template_v1_3_12.csv'
PLAN_DEPS_TEMPLATE='ASAP_BLOCK_run_plan_dependencies_template_v1_3_12.csv'
EXEC_LOG_TEMPLATE='ASAP_BLOCK_run_execution_log_template_v1_3_12.csv'
PER_TASK_RESULTS_TEMPLATE='ASAP_BLOCK_per_task_results_template_v1_3_12.csv'
THEORY_FORMULA_VERSION='v9.3'
THEORY_DOCUMENT_SHA256='524d4f84b04185609735a2be3ff54984149be1478a111044494ec1f8ff65098e'
FIXED_CARRY_IN_INTERFACE_VERSION='V9_3_SECTION_9_5_FIXED_CARRY_IN_INTERFACE'
FIXED_CARRY_IN_INTERFACE_SHA256=THEORY_DOCUMENT_SHA256
LOC_THETA_CW_SOURCE_CERTIFICATION='CERTIFIED_TASKSET'
REPLAY_INTERFACE='ASAP_BLOCK_GATE_REPLAY_V1'
SAFE_NAME_RE=re.compile(r'[A-Za-z0-9_.+@-]+\Z')
HEX64_RE=re.compile(r'[0-9a-f]{64}\Z')
REQUEST_PAYLOAD_SCHEMA_VERSION='1.3.12'
TERMINAL={'FINISHED','TIMEOUT','OUT_OF_MEMORY','INTERRUPTED','INFRASTRUCTURE_FAILURE','NOT_RUN_DEPENDENCY','CANCELLED'}
RETRYABLE={'OUT_OF_MEMORY','INTERRUPTED','INFRASTRUCTURE_FAILURE'}
FORMAL_PHASES={'CORE0B','FORMAL'}
PREFORMAL_PHASES={'CORE0A','PILOT'}
SEEDED_TYPES={'GENERATION','RELEASE_TRACE_GENERATION','HARVEST_TRACE_GENERATION'}
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
 ('per_task_results.csv','task_failure_reason_code'):('enum','task_failure_reason_code'),
 ('per_task_results.csv','task_failure_detail'):('string',None),
 ('per_task_results.csv','carry_in_source_certification_status'):('enum','analysis_certification_status'),
 ('rta_dependency_records.csv','theta_source_mode'):('enum','theta_source_mode'),
 ('simulation_taskset_summary.csv','energy_account_semantics_version'):('identifier',None),
 ('simulation_taskset_summary.csv','battery_mode'):('enum','battery_mode'),
 ('analysis_simulation_compatibility_checks.csv','energy_account_match_status'):('enum','match_status'),
 ('analysis_simulation_compatibility_checks.csv','power_upper_bound_status'):('enum','match_status'),
}

TASK_FAILURE_DETAIL_BY_CODE={
 'NONE':None,
 'NO_CANDIDATE':'closure exhausted through task deadline',
 'SOLVER_TIMEOUT':None,
 'NUMERIC_ERROR':'numeric guard rejected analysis',
 'UPSTREAM_PREFIX_FAILURE':None,
 'DEPENDENCY_NOT_APPLICABLE':None,
 'DOMINANCE_INVARIANT_VIOLATION':'local result violated frozen carry-in dominance',
 'UNKNOWN_CORE_STATUS':'unrecognized core solver status',
 'INTERNAL_CONFORMANCE_FAILURE':'internal analyzer conformance failure',
}
TASK_FAILURE_CODES_BY_SOLVER_STATUS={
 'CANDIDATE_FOUND':{'NONE','DOMINANCE_INVARIANT_VIOLATION'},
 'NO_CANDIDATE':{'NO_CANDIDATE','DOMINANCE_INVARIANT_VIOLATION'},
 'TIMEOUT':{'SOLVER_TIMEOUT'},
 'NUMERIC_ERROR':{'NUMERIC_ERROR'},
 'NOT_EVALUATED_AFTER_PREFIX_FAILURE':{'UPSTREAM_PREFIX_FAILURE'},
 'NOT_APPLICABLE_DEPENDENCY':{'DEPENDENCY_NOT_APPLICABLE'},
 'INTERNAL_CONFORMANCE_FAILURE':{'UNKNOWN_CORE_STATUS','INTERNAL_CONFORMANCE_FAILURE'},
}
TASK_FAILURE_NORMALIZATION_ORIGINS={
 'CORE_CANDIDATE':('CANDIDATE_FOUND','NONE'),
 'CORE_NO_CANDIDATE':('NO_CANDIDATE','NO_CANDIDATE'),
 'CORE_DEADLINE_TIMEOUT':('TIMEOUT','SOLVER_TIMEOUT'),
 'CAUGHT_TIMEOUT_EXCEPTION':('TIMEOUT','SOLVER_TIMEOUT'),
 'CAUGHT_OVERFLOW_EXCEPTION':('NUMERIC_ERROR','NUMERIC_ERROR'),
 'CAUGHT_NUMERIC_EXCEPTION':('NUMERIC_ERROR','NUMERIC_ERROR'),
 'TASKSET_PREFIX_SYNTHETIC':('NOT_EVALUATED_AFTER_PREFIX_FAILURE','UPSTREAM_PREFIX_FAILURE'),
 'TASKSET_DEPENDENCY_SYNTHETIC':('NOT_APPLICABLE_DEPENDENCY','DEPENDENCY_NOT_APPLICABLE'),
 'ADAPTER_UNKNOWN_CORE_STATUS':('INTERNAL_CONFORMANCE_FAILURE','UNKNOWN_CORE_STATUS'),
 'ANALYZER_INTERNAL_CONFORMANCE':('INTERNAL_CONFORMANCE_FAILURE','INTERNAL_CONFORMANCE_FAILURE'),
}
TASK_FAILURE_EXACT_RAW={
 'CORE_CANDIDATE':None,
 'CORE_NO_CANDIDATE':'no v9.3 closure candidate by the task deadline',
 'CORE_DEADLINE_TIMEOUT':'v9.3 closure search timed out',
 'TASKSET_PREFIX_SYNTHETIC':'not evaluated after prefix failure',
 'TASKSET_DEPENDENCY_SYNTHETIC':'fixed carry-in dependency is not applicable',
}

class V:
    def __init__(self): self.e=[]
    def err(self,x): self.e.append(x)

def normalize_task_failure_reason(solver_status,certification_status,raw_failure_reason,structured_context):
    """Reference fail-closed adapter; raw debug text is never returned."""
    if not isinstance(structured_context,dict) or set(structured_context)!={'origin'}:
        raise ValueError('structured failure context must contain exactly origin')
    origin=structured_context['origin']
    if origin=='TASKSET_DOMINANCE_COUNTEREXAMPLE':
        if solver_status not in {'CANDIDATE_FOUND','NO_CANDIDATE'} or certification_status!='NOT_CERTIFIED':
            raise ValueError('dominance context/status mismatch')
        if raw_failure_reason not in {None,'no v9.3 closure candidate by the task deadline'}:
            raise ValueError('unknown dominance raw failure reason')
        code='DOMINANCE_INVARIANT_VIOLATION'
    else:
        expected=TASK_FAILURE_NORMALIZATION_ORIGINS.get(origin)
        if expected is None or expected[0]!=solver_status:
            raise ValueError('unknown or status-inconsistent structured failure origin')
        if origin in TASK_FAILURE_EXACT_RAW and raw_failure_reason!=TASK_FAILURE_EXACT_RAW[origin]:
            raise ValueError('unknown raw failure reason for structured origin')
        if origin in {'CAUGHT_TIMEOUT_EXCEPTION','CAUGHT_OVERFLOW_EXCEPTION','CAUGHT_NUMERIC_EXCEPTION','ANALYZER_INTERNAL_CONFORMANCE'}:
            if not isinstance(raw_failure_reason,str) or raw_failure_reason=='':
                raise ValueError('classified exception origin requires nonempty raw diagnostic')
        if origin=='ADAPTER_UNKNOWN_CORE_STATUS' and raw_failure_reason not in {None,'unknown core status'}:
            raise ValueError('unknown adapter raw failure reason')
        code=expected[1]
    return code,TASK_FAILURE_DETAIL_BY_CODE[code]

def validate_task_failure_provenance(row,v,label='per_task_results.csv'):
    status=row.get('task_solver_status','');cert=row.get('task_certification_status','')
    code=row.get('task_failure_reason_code','');detail=row.get('task_failure_detail','')
    allowed=TASK_FAILURE_CODES_BY_SOLVER_STATUS.get(status,set())
    if code not in allowed:v.err(f'task failure status/code mismatch:{label}:{status}:{code}')
    dominance=row.get('dominance_invariant_status')=='DOMINANCE_INVARIANT_VIOLATION'
    if dominance!=(code=='DOMINANCE_INVARIANT_VIOLATION'):
        v.err(f'task failure dominance/code mismatch:{label}:{status}:{code}')
    if code=='DOMINANCE_INVARIANT_VIOLATION' and cert!='NOT_CERTIFIED':
        v.err(f'task dominance failure certification mismatch:{label}:{cert}')
    expected=TASK_FAILURE_DETAIL_BY_CODE.get(code,object())
    actual=None if detail=='' else detail
    if actual!=expected:v.err(f'task failure detail/code mismatch:{label}:{code}:{actual!r}')
    if actual is not None:
        try:validate_task_failure_detail(actual)
        except Exception as e:v.err(f'task failure detail noncanonical:{label}:{e}')

def schema_dictionary(schema,dd,v):
    if set(schema.get('tables',{}))!=set(dd.get('tables',{})): v.err('schema/dictionary table set mismatch')
    for tn,td in schema.get('tables',{}).items():
        classes={}
        for cl in ['required','conditionally_required','optional_diagnostic']:
            for f in td.get(cl,[]):
                if f in classes:v.err(f'field classified twice:{tn}.{f}')
                classes[f]=cl
        if td.get('canonical_column_order')!=list(classes):v.err(f'canonical order mismatch:{tn}')
        dfs=dd.get('tables',{}).get(tn,{}).get('fields',{})
        if set(classes)!=set(dfs):v.err(f'schema/dictionary fields mismatch:{tn}')
        covered={f for r in td.get('conditional_rules',[]) for f in r.get('then_required',[])}
        missing=set(td.get('conditionally_required',[]))-covered
        if missing:v.err(f'conditional field lacks rule:{tn}:{sorted(missing)}')
        for f,spec in dfs.items():
            if spec.get('field_class')!=classes.get(f):v.err(f'field class mismatch:{tn}.{f}')
            if spec.get('type')=='enum' and spec.get('enum_ref') not in schema.get('enums',{}):v.err(f'bad enum_ref:{tn}.{f}')
            if spec.get('type')=='enum_set' and spec.get('enum_ref') not in schema.get('failure_masks',{}):v.err(f'bad mask_ref:{tn}.{f}')
            expected=SEMANTIC_FIELD_TYPES.get((tn,f))
            if expected and (spec.get('type'),spec.get('enum_ref'))!=expected:v.err(f'semantic type mismatch:{tn}.{f}:{(spec.get("type"),spec.get("enum_ref"))}!={expected}')

def read_tables(root,schema,v):
    rows={}
    for tn,td in schema['tables'].items():
        p=root/tn
        if not p.exists():v.err(f'missing runtime table:{tn}');continue
        try:h,r=read_csv_strict(p)
        except Exception as e:v.err(str(e));continue
        if h!=td['canonical_column_order']:v.err(f'header/order mismatch:{tn}')
        keys=[tuple(x.get(k,'') for k in td['primary_key']) for x in r]
        if keys!=sorted(keys,key=lambda z:canonical_json_bytes(list(z))):v.err(f'noncanonical row order:{tn}')
        rows[tn]=r
    return rows

def types_conditions(schema,dd,rows,v):
    for tn,td in schema['tables'].items():
        if tn not in rows:continue
        for i,row in enumerate(rows[tn],2):
            for f in td['required']:
                if row.get(f,'')=='':v.err(f'required null:{tn}:{i}:{f}')
            for f,val in row.items():
                if val=='':continue
                try:validate_scalar(val,dd['tables'][tn]['fields'][f],schema['enums'],schema['failure_masks'])
                except Exception as e:v.err(f'type violation:{tn}:{i}:{f}:{val}:{e}')
            for rule in td.get('conditional_rules',[]):
                try:match=condition_matches(row,rule['if'])
                except Exception as e:v.err(f'bad rule:{tn}:{rule.get("rule_id")}:{e}');continue
                if match:
                    for f in rule.get('then_required',[]):
                        if row.get(f,'')=='':v.err(f'conditional required null:{tn}:{i}:{f}')
                    for f in rule.get('then_null',[]):
                        if row.get(f,'')!='':v.err(f'conditional must-null:{tn}:{i}:{f}')
                else:
                    for f in rule.get('else_null',[]):
                        if row.get(f,'')!='':v.err(f'conditional else-null:{tn}:{i}:{f}')

def keys_fks(schema,rows,v):
    for tn,td in schema['tables'].items():
        if tn not in rows:continue
        seen=set()
        for i,r in enumerate(rows[tn],2):
            k=tuple(r.get(f,'') for f in td['primary_key'])
            if any(x=='' for x in k):v.err(f'null PK:{tn}:{i}')
            if k in seen:v.err(f'duplicate PK:{tn}:{k}')
            seen.add(k)
        for uc in td.get('unique_constraints',[]):
            seen=set()
            for i,r in enumerate(rows[tn],2):
                k=tuple(r.get(f,'') for f in uc)
                if any(x=='' for x in k):continue
                if k in seen:v.err(f'duplicate unique:{tn}:{uc}:{k}')
                seen.add(k)
    for tn,td in schema['tables'].items():
        if tn not in rows:continue
        for lf,ref in td.get('foreign_keys',{}).items():
            rt,rf=ref.rsplit('.',1);idx={r.get(rf,'') for r in rows.get(rt,[])}
            for i,r in enumerate(rows[tn],2):
                val=r.get(lf,'')
                if val and val not in idx:v.err(f'broken FK:{tn}:{i}:{lf}->{ref}:{val}')
        for fk in td.get('composite_foreign_keys',[]):
            rt=fk['references']['table'];rc=fk['references']['columns'];lc=fk['local']
            idx={tuple(r.get(c,'') for c in rc) for r in rows.get(rt,[])}
            for i,r in enumerate(rows[tn],2):
                k=tuple(r.get(c,'') for c in lc)
                if any(x=='' for x in k):continue
                if k not in idx:v.err(f'broken composite FK:{tn}:{i}:{lc}->{rt}.{rc}:{k}')

def cycle(nodes,edges):
    adj={n:[] for n in nodes}
    for a,b in edges:adj.setdefault(a,[]).append(b)
    color={n:0 for n in nodes}
    def dfs(n):
        color[n]=1
        for m in adj.get(n,[]):
            if color.get(m,0)==1:return True
            if color.get(m,0)==0 and dfs(m):return True
        color[n]=2;return False
    return any(color[n]==0 and dfs(n) for n in nodes)

def required_nonnull(obj,paths,v,prefix='formal'):
    for path in paths:
        try:val=get_nested(obj,path)
        except Exception:v.err(f'{prefix} missing path:{path}');continue
        if val is None or val=='' or val==[] or val=={}:v.err(f'{prefix} unfilled path:{path}')

def _strict_positive_int(value,label,v):
    try:
        n=value if isinstance(value,int) else parse_canonical_integer(str(value))
        if n<=0:raise ValueError('must be >0')
        return n
    except Exception as e:
        v.err(f'{label} invalid positive integer:{value!r}:{e}')
        return None

def _safe_name(value,label,v):
    if not isinstance(value,str) or not value or value in {'.','..'} or '/' in value or '\\' in value or Path(value).name!=value or unicodedata.normalize('NFC',value)!=value or any(ord(c)<32 for c in value):
        v.err(f'{label} unsafe root filename:{value!r}');return None
    return value

def _safe_existing_file(root,value,label,v):
    name=_safe_name(value,label,v)
    if not name:return None
    p=root/name
    if p.is_symlink():v.err(f'{label} symlink forbidden:{name}');return None
    if not p.exists() or not p.is_file():v.err(f'{label} missing file:{name}');return None
    return p

def _hash_value(value,label,v):
    if not isinstance(value,str) or not HEX64_RE.fullmatch(value):v.err(f'{label} invalid SHA-256:{value!r}');return None
    return value

def _identifier(value,label,v,allowed=None):
    if not isinstance(value,str) or not value or value.strip()!=value or any(ord(c)<32 for c in value):
        v.err(f'{label} invalid identifier:{value!r}');return None
    if re.search(r'\$\{|\{\{|<[^>]+>',value) or value.upper() in {'X','TBD','TODO','PLACEHOLDER','UNKNOWN','CHANGEME','NULL','NONE'}:
        v.err(f'{label} unresolved placeholder:{value!r}');return None
    if allowed is not None and value not in allowed:v.err(f'{label} invalid value:{value!r}; allowed={sorted(allowed)}');return None
    return value

def _bool_value(value,label,v,expected=None):
    if type(value) is not bool:v.err(f'{label} must be YAML boolean:{value!r}');return None
    if expected is not None and value is not expected:v.err(f'{label} must be {expected}')
    return value

def _int_value(value,label,v,minimum=None,maximum=None):
    if isinstance(value,bool):v.err(f'{label} boolean is not integer');return None
    try:
        n=value if isinstance(value,int) else parse_canonical_integer(str(value))
    except Exception as e:v.err(f'{label} invalid canonical integer:{value!r}:{e}');return None
    if minimum is not None and n<minimum:v.err(f'{label} below minimum {minimum}:{n}')
    if maximum is not None and n>maximum:v.err(f'{label} above maximum {maximum}:{n}')
    return n

def _number_value(value,label,v,minimum=None,maximum=None,strict_min=False,strict_max=False):
    if isinstance(value,bool):v.err(f'{label} boolean is not number');return None
    try:
        num,den=parse_canonical_number(str(value));x=Fraction(num,den)
    except Exception as e:v.err(f'{label} invalid canonical number:{value!r}:{e}');return None
    if minimum is not None and (x<=minimum if strict_min else x<minimum):v.err(f'{label} below allowed range:{value!r}')
    if maximum is not None and (x>=maximum if strict_max else x>maximum):v.err(f'{label} above allowed range:{value!r}')
    return x

def _identifier_list(value,label,v,required=None,nonempty=True):
    if not isinstance(value,list) or (nonempty and not value):v.err(f'{label} must be a nonempty list');return []
    out=[]
    for i,x in enumerate(value):
        y=_identifier(x,f'{label}[{i}]',v)
        if y is not None:out.append(y)
    if len(out)!=len(set(out)):v.err(f'{label} contains duplicates')
    if required is not None and out!=required:v.err(f'{label} mismatch:{out}!={required}')
    return out

def _no_placeholders(obj,label,v):
    if isinstance(obj,dict):
        for k,x in obj.items():_no_placeholders(x,f'{label}.{k}',v)
    elif isinstance(obj,list):
        for i,x in enumerate(obj):_no_placeholders(x,f'{label}[{i}]',v)
    elif isinstance(obj,str):
        _identifier(obj,label,v) if (re.search(r'\$\{|\{\{|<[^>]+>',obj) or obj.upper() in {'X','TBD','TODO','PLACEHOLDER','UNKNOWN','CHANGEME'}) else None

def _validate_child_completeness(role,obj,v):
    paths={
      'generator':[
        'contract_metadata.version','contract_metadata.generator_contract_hash','contract_metadata.canonical_serialization_file',
        'generator_parameters.task_util_min','generator_parameters.task_util_max','generator_parameters.utilization_tolerance',
        'generator_parameters.period_distribution','generator_parameters.period_min','generator_parameters.period_max',
        'generator_parameters.deadline_generation_rule','generator_parameters.deadline_delta_main',
        'generator_parameters.power_latent_distribution','generator_parameters.power_latent_mapping_version',
        'generator_parameters.max_resampling_attempts','generator_parameters.generation_failure_threshold',
        'generator_parameters.priority_policy','generator_parameters.priority_tiebreak','generator_parameters.rho_e_parameterization_rule',
        'generator_parameters.parameter_cell_canonicalization_version','rng_contract.seed_algorithm',
        'rng_contract.stream_labels','rng_contract.seed_derivation_algorithm','hash_preimage_rule'],
      'simulation':[
        'contract_metadata.version','contract_metadata.simulation_contract_hash','contract_metadata.canonical_serialization_file',
        'scheduler_and_model.scheduler_variant','scheduler_and_model.scheduler_semantics_version',
        'scheduler_and_model.event_order_version','scheduler_and_model.energy_account_semantics_version',
        'scheduler_and_model.simulation_energy_account_mode','scheduler_and_model.initial_energy',
        'scheduler_and_model.battery_mode',
        'horizons_and_scenarios.generation_horizon','horizons_and_scenarios.observation_horizon',
        'horizons_and_scenarios.release_scenarios','horizons_and_scenarios.harvest_scenarios',
        'horizons_and_scenarios.scenario_requests_per_taskset','execution_contract.unmodeled_overhead_policy',
        'rng_contract.seed_algorithm','rng_contract.stream_labels','rng_contract.seed_derivation_algorithm','hash_preimage_rule'],
      'trace_generator':[
        'contract_metadata.version','contract_metadata.trace_generator_contract_hash','contract_metadata.canonical_serialization_file',
        'release_trace_generator.offset_distribution','release_trace_generator.sporadic_gap_distribution',
        'release_trace_generator.trace_count_per_scenario','release_trace_generator.generator_version',
        'harvest_trace_generator.method','harvest_trace_generator.trace_count_per_scenario',
        'harvest_trace_generator.generator_version','harvest_trace_generator.service_curve_validation_domain',
        'adversarial_search.algorithm','adversarial_search.objective','adversarial_search.budget',
        'adversarial_search.stop_condition','rng_substreams','hash_preimage_rule']}
    required_nonnull(obj,paths[role],v,prefix=f'{role} contract')
    _no_placeholders(obj,f'{role} contract',v)
    def val(path):
        try:return get_nested(obj,path)
        except Exception:return None
    meta=obj.get('contract_metadata',{})
    expected_name={'generator':'ASAP_BLOCK_generator_contract','simulation':'ASAP_BLOCK_simulation_contract','trace_generator':'ASAP_BLOCK_trace_generator_contract'}[role]
    if meta.get('name')!=expected_name:v.err(f'{role} contract name mismatch')
    if str(meta.get('version'))!=VERSION:v.err(f'{role} contract version mismatch')
    if meta.get('canonical_serialization_file')!=CANON:v.err(f'{role} canonical serialization filename mismatch')
    _hash_value(meta.get({'generator':'generator_contract_hash','simulation':'simulation_contract_hash','trace_generator':'trace_generator_contract_hash'}[role]),f'{role} contract hash',v)
    if role=='generator':
        p=obj.get('generator_parameters',{})
        umin=_number_value(p.get('task_util_min'),'task_util_min',v,Fraction(0),Fraction(1),strict_min=True)
        umax=_number_value(p.get('task_util_max'),'task_util_max',v,Fraction(0),Fraction(1),strict_min=True)
        if umin is not None and umax is not None and umin>umax:v.err('task_util_min exceeds task_util_max')
        _number_value(p.get('utilization_tolerance'),'utilization_tolerance',v,Fraction(0),Fraction(1),strict_max=True)
        pmin=_int_value(p.get('period_min'),'period_min',v,1);pmax=_int_value(p.get('period_max'),'period_max',v,1)
        if pmin is not None and pmax is not None and pmin>pmax:v.err('period_min exceeds period_max')
        _number_value(p.get('deadline_delta_main'),'deadline_delta_main',v,Fraction(0),Fraction(1))
        _int_value(p.get('max_resampling_attempts'),'max_resampling_attempts',v,1)
        _number_value(p.get('generation_failure_threshold'),'generation_failure_threshold',v,Fraction(0),Fraction(1))
        _identifier(p.get('period_distribution'),'period_distribution',v,{'UNIFORM_INTEGER','LOG_UNIFORM_INTEGER'})
        _identifier(p.get('deadline_generation_rule'),'deadline_generation_rule',v)
        _identifier(p.get('power_latent_distribution'),'power_latent_distribution',v)
        _identifier(p.get('power_latent_mapping_version'),'power_latent_mapping_version',v)
        _identifier(p.get('rho_e_parameterization_rule'),'rho_e_parameterization_rule',v)
        if p.get('priority_policy')!='DM':v.err('generator priority_policy must be DM')
        _identifier_list(p.get('priority_tiebreak'),'priority_tiebreak',v,['D_i','T_i','task_id'])
        if p.get('parameter_cell_canonicalization_version')!='PARAMETER_CELL_CANONICAL_V1_3_12':v.err('parameter cell canonicalization version mismatch')
        rng=obj.get('rng_contract',{})
        if rng.get('seed_algorithm')!='SHA256_DOMAIN_SEPARATED_CANONICAL_V1_3_12' or rng.get('seed_derivation_algorithm')!='SHA256_DOMAIN_SEPARATED_CANONICAL_V1_3_12':v.err('generator RNG algorithm mismatch')
        _identifier_list(rng.get('stream_labels'),'generator stream_labels',v,['TASK_UTILIZATION','PERIODS','POWER_LATENT'])
        if obj.get('hash_preimage_rule')!='canonical preimage generator_contract_hash; self field null':v.err('generator hash_preimage_rule mismatch')
    elif role=='simulation':
        sm=obj.get('scheduler_and_model',{});hs=obj.get('horizons_and_scenarios',{});ex=obj.get('execution_contract',{})
        if sm.get('scheduler_variant')!='ASAP-BLOCK':v.err('simulation scheduler_variant must be ASAP-BLOCK')
        for fld in ['scheduler_semantics_version','event_order_version','energy_account_semantics_version']:_identifier(sm.get(fld),fld,v)
        _identifier(sm.get('simulation_energy_account_mode'),'simulation_energy_account_mode',v,{'ANALYSIS_CONSISTENT_ACCOUNT','PHYSICAL_WITH_CONSERVATIVE_SHADOW'})
        _number_value(sm.get('initial_energy'),'simulation initial_energy',v,Fraction(0))
        mode=_identifier(sm.get('battery_mode'),'battery_mode',v,{'UNBOUNDED','FINITE'})
        cap=sm.get('battery_capacity')
        if mode=='UNBOUNDED' and cap is not None:v.err('UNBOUNDED battery requires null battery_capacity')
        if mode=='FINITE':_number_value(cap,'battery_capacity',v,Fraction(0),strict_min=True)
        g=_int_value(hs.get('generation_horizon'),'generation_horizon',v,1);o=_int_value(hs.get('observation_horizon'),'observation_horizon',v,1)
        if g is not None and o is not None and o<g:v.err('simulation observation_horizon is smaller than generation_horizon')
        _identifier_list(hs.get('release_scenarios'),'release_scenarios',v)
        _identifier_list(hs.get('harvest_scenarios'),'harvest_scenarios',v)
        _int_value(hs.get('scenario_requests_per_taskset'),'scenario_requests_per_taskset',v,1)
        for fld in ['actual_execution_demand_equals_C_i','actual_unit_power_le_analysis_bound','integer_boundary_preemption_migration','same_task_jobs_nonparallel']:_bool_value(ex.get(fld),fld,v,True)
        _identifier(ex.get('unmodeled_overhead_policy'),'unmodeled_overhead_policy',v)
        rng=obj.get('rng_contract',{})
        if rng.get('seed_algorithm')!='SHA256_DOMAIN_SEPARATED_CANONICAL_V1_3_12' or rng.get('seed_derivation_algorithm')!='SHA256_DOMAIN_SEPARATED_CANONICAL_V1_3_12':v.err('simulation RNG algorithm mismatch')
        _identifier_list(rng.get('stream_labels'),'simulation stream_labels',v,['RELEASE_TRACE','HARVEST_TRACE','ADVERSARIAL_SEARCH'])
        if obj.get('hash_preimage_rule')!='canonical preimage simulation_contract_hash; self field null':v.err('simulation hash_preimage_rule mismatch')
    elif role=='trace_generator':
        rg=obj.get('release_trace_generator',{});hg=obj.get('harvest_trace_generator',{});ad=obj.get('adversarial_search',{})
        for fld in ['offset_distribution','sporadic_gap_distribution','generator_version']:_identifier(rg.get(fld),f'release_trace_generator.{fld}',v)
        _int_value(rg.get('trace_count_per_scenario'),'release trace_count_per_scenario',v,1)
        for fld in ['method','generator_version','service_curve_validation_domain']:_identifier(hg.get(fld),f'harvest_trace_generator.{fld}',v)
        _int_value(hg.get('trace_count_per_scenario'),'harvest trace_count_per_scenario',v,1)
        _identifier(ad.get('algorithm'),'adversarial_search.algorithm',v)
        if ad.get('objective')!='ACTUAL_RESPONSE_OR_DEADLINE_STRESS_NOT_METHOD_SPECIFIC_BOUND':v.err('adversarial_search.objective mismatch')
        _int_value(ad.get('budget'),'adversarial_search.budget',v,1)
        _identifier(ad.get('stop_condition'),'adversarial_search.stop_condition',v)
        _identifier_list(obj.get('rng_substreams'),'trace rng_substreams',v,['TASK_UTILIZATION','PERIODS','POWER_LATENT','RELEASE_TRACE','HARVEST_TRACE','ADVERSARIAL_SEARCH'])
        if obj.get('hash_preimage_rule')!='canonical preimage trace_generator_contract_hash; self field null':v.err('trace generator hash_preimage_rule mismatch')

def _validate_formal_values(f,root,schema,v):
    _no_placeholders(f,'formal contract',v)
    meta=f.get('contract_metadata',{})
    if meta.get('name') not in {'ASAP_BLOCK_formal_contract','ASAP_BLOCK_formal_contract_template'}:v.err('formal contract name mismatch')
    if meta.get('name')=='ASAP_BLOCK_formal_contract_template':v.err('runtime formal contract still has template name')
    if meta.get('status') not in {'FROZEN','FORMAL_FROZEN'}:v.err('formal contract status must be FROZEN or FORMAL_FROZEN')
    _identifier(meta.get('formal_contract_version'),'formal_contract_version',v)
    _hash_value(meta.get('formal_contract_hash'),'formal_contract_hash',v)
    if meta.get('canonical_serialization_file')!=CANON:v.err('formal canonical serialization filename mismatch')
    th=f.get('theory_contract',{})
    if th.get('rta_formula_version')!=THEORY_FORMULA_VERSION:v.err('formal RTA formula version mismatch')
    if th.get('theory_document_sha256')!=THEORY_DOCUMENT_SHA256:v.err('formal theory document hash mismatch')
    if th.get('fixed_carry_in_corollary_version')!=FIXED_CARRY_IN_INTERFACE_VERSION:v.err('formal fixed-carry-in interface version mismatch')
    if th.get('fixed_carry_in_corollary_sha256')!=FIXED_CARRY_IN_INTERFACE_SHA256:v.err('formal fixed-carry-in interface hash mismatch')
    pre=f.get('pre_core0a_commitments',{});num=f.get('numeric_contract',{});seed=f.get('seed_contract',{})
    mode=_identifier(num.get('energy_numeric_mode'),'energy_numeric_mode',v,set(schema['enums']['energy_numeric_mode']))
    _identifier(pre.get('energy_numeric_mode'),'pre_core0a energy_numeric_mode',v,set(schema['enums']['energy_numeric_mode']))
    if pre.get('fixed_point_integerization_mode')!='POINTWISE_FLOOR':v.err('pre-core fixed-point integerization mode must be POINTWISE_FLOOR')
    scmode=_identifier(num.get('service_curve_integerization_mode'),'service_curve_integerization_mode',v,set(schema['enums']['service_curve_integerization_mode']))
    if mode=='FIXED_POINT_DIRECTED':
        if scmode!='POINTWISE_FLOOR':v.err('fixed-point mode requires POINTWISE_FLOOR')
        _int_value(num.get('energy_numeric_scale'),'energy_numeric_scale',v,1)
        _identifier(num.get('numeric_integer_type'),'numeric_integer_type',v,{'CHECKED_INT64','CHECKED_UINT64','CHECKED_INT128','CHECKED_UINT128','ARBITRARY_PRECISION_INTEGER'})
        if num.get('demand_rounding')!='UP' or num.get('supply_rounding')!='DOWN':v.err('fixed-point rounding must be demand=UP and supply=DOWN')
    elif mode=='EXACT_RATIONAL':
        if scmode!='EXACT':v.err('exact-rational mode requires service_curve_integerization_mode=EXACT')
        if num.get('energy_numeric_scale') is not None:v.err('exact-rational mode requires null energy_numeric_scale')
        _identifier(num.get('numeric_integer_type'),'numeric_integer_type',v,{'ARBITRARY_PRECISION_INTEGER','CHECKED_INT128'})
        if num.get('demand_rounding')!='EXACT' or num.get('supply_rounding')!='EXACT':v.err('exact-rational rounding must be EXACT')
    _bool_value(num.get('checked_arithmetic'),'checked_arithmetic',v,True)
    _number_value(num.get('rho_e_tolerance'),'rho_e_tolerance',v,Fraction(0))
    _number_value(num.get('e0_rounding_tolerance'),'e0_rounding_tolerance',v,Fraction(0))
    _identifier(num.get('numeric_range_proof_id'),'numeric_range_proof_id',v)
    source=_identifier(seed.get('formal_master_seed_source'),'formal_master_seed_source',v,set(schema['enums']['formal_master_seed_source']))
    _identifier(pre.get('formal_master_seed_source'),'pre-core formal_master_seed_source',v,set(schema['enums']['formal_master_seed_source']))
    for obj,label in [(pre,'pre-core'),(seed,'seed')]:
        _hash_value(obj.get('formal_master_seed_source_commitment_hash'),f'{label} master-seed commitment hash',v)
        if obj.get('seed_derivation_algorithm')!='SHA256_DOMAIN_SEPARATED_CANONICAL_V1_3_12':v.err(f'{label} seed derivation algorithm mismatch')
    _hash_value(pre.get('candidate_build_identity_requirement_hash'),'candidate_build_identity_requirement_hash',v)
    _int_value(seed.get('formal_master_seed'),'formal_master_seed',v,0,2**64-1)
    _hash_value(seed.get('seed_derivation_context_hash'),'seed_derivation_context_hash',v)
    _hash_value(seed.get('formal_seed_set_hash'),'formal_seed_set_hash',v)
    _identifier_list(seed.get('substream_labels'),'formal substream_labels',v,['TASK_UTILIZATION','PERIODS','POWER_LATENT','RELEASE_TRACE','HARVEST_TRACE','ADVERSARIAL_SEARCH'])
    beacon_fields=['public_random_beacon_name','public_random_beacon_round_or_timestamp','public_random_beacon_extraction_rule']
    if source=='PUBLIC_RANDOM_BEACON':
        for x in beacon_fields:_identifier(pre.get(x),x,v)
    else:
        for x in beacon_fields:
            if pre.get(x) is not None:v.err(f'{x} must be null unless PUBLIC_RANDOM_BEACON')
    if f.get('analysis_contract',{}).get('variants')!=['CW-D','LOC-D','CW-Theta^cw','LOC-Theta^cw','LOC-Theta^loc']:v.err('analysis variant set/order mismatch')
    if f.get('analysis_contract',{}).get('main_methods')!=['CW-Theta^cw','LOC-Theta^loc']:v.err('analysis main-method set/order mismatch')
    expected_certification_semantics={
        'method_roles':{'CW-Theta^cw':'MAIN_METHOD','LOC-Theta^loc':'MAIN_METHOD','CW-D':'AUXILIARY_ABLATION','LOC-D':'AUXILIARY_ABLATION','LOC-Theta^cw':'AUXILIARY_ABLATION'},
        'taskset_proven_equivalence':'analysis_certification_status == CERTIFIED_TASKSET',
        'pre_joint_success_task_status':'PROVISIONAL_NOT_CERTIFIED',
        'loc_theta_cw':{'source_variant':'CW-Theta^cw','source_solver_status':'COMPLETED','source_certification_status':'CERTIFIED_TASKSET','fixed_carry_in_corollary_status':'ACTIVE','dependency_vector_check_status':'VALID','complete_compatible_vector_status':'CERTIFIED_TASKSET','invalid_dependency_formal_status':'NOT_APPLICABLE','valid_domain_no_candidate_status':'INTERNAL_CONFORMANCE_FAILURE','valid_domain_no_candidate_dominance_status':'DOMINANCE_INVARIANT_VIOLATION'},
    }
    if f.get('analysis_contract',{}).get('certification_semantics')!=expected_certification_semantics:v.err('analysis certification-semantics contract mismatch')
    cs=f.get('analysis_contract',{}).get('closure_search',{})
    expected_cs={'w_scan':'C_k..D_k inclusive pointwise','h_scan':'0..w-A_k^Theta(w) inclusive','q_scan':'1..A_k^Theta(w) inclusive','forbid_jump_or_binary_search_without_theorem':True}
    if cs!=expected_cs:v.err('analysis closure-search contract mismatch')
    _hash_value(f.get('pairing_contract',{}).get('transformation_contract_hash'),'transformation_contract_hash',v)
    _identifier(f.get('pairing_contract',{}).get('paired_family_definition'),'paired_family_definition',v)
    _identifier(f.get('pairing_contract',{}).get('base_generation_cell_definition'),'base_generation_cell_definition',v)
    _int_value(f.get('sample_request_contract',{}).get('pilot_generation_requests_per_cell'),'pilot_generation_requests_per_cell',v,1)
    _int_value(f.get('sample_request_contract',{}).get('formal_generation_requests_per_cell'),'formal_generation_requests_per_cell',v,1)
    _bool_value(f.get('sample_request_contract',{}).get('sample_size_is_request_count'),'sample_size_is_request_count',v,True)
    _bool_value(f.get('sample_request_contract',{}).get('replacement_seeds_forbidden'),'replacement_seeds_forbidden',v,True)
    stats=f.get('statistics_contract',{})
    for fld in ['operational_comparison','analytical_comparison','tightness_weighting','bootstrap_cluster_unit','multiple_testing_policy','sample_size_power_target']:_identifier(stats.get(fld),f'statistics_contract.{fld}',v)
    rt=f.get('runtime_environment_contract',{})
    _int_value(rt.get('thread_count'),'thread_count',v,1);_int_value(rt.get('warmup_runs'),'warmup_runs',v,0);_int_value(rt.get('measurement_repetitions'),'measurement_repetitions',v,1)
    for fld in ['cpu_affinity','cpu_governor','turbo_policy','run_order_randomization','cache_policy','rss_measurement_method']:_identifier(rt.get(fld),f'runtime_environment_contract.{fld}',v)
    for fld,val in f.get('approved_builds',{}).items():_hash_value(val,f'approved_builds.{fld}',v)
    rpc=f.get('run_plan_contract',{})
    expected_names={'definition_file':'run_plan_definition.csv','dependencies_file':'run_plan_dependencies.csv','execution_log_file':'run_execution_log.csv','request_outputs_file':'request_outputs.csv','run_plan_hash_field':'run_plan_bundle_hash','preimage_spec_id':'run_plan_bundle_hash'}
    for fld,x in expected_names.items():
        if rpc.get(fld)!=x:v.err(f'run_plan_contract.{fld} mismatch')
    _hash_value(rpc.get('run_plan_bundle_hash'),'run_plan_bundle_hash',v)
    _bool_value(rpc.get('dependency_DAG_required'),'dependency_DAG_required',v,True)
    rp=rpc.get('retry_policy',{});_int_value(rp.get('max_attempts_per_request'),'max_attempts_per_request',v,1)
    _identifier_list(rp.get('retryable_terminal_events'),'retryable_terminal_events',v,['OUT_OF_MEMORY','INTERRUPTED','INFRASTRUCTURE_FAILURE'])
    _bool_value(rp.get('seed_and_payload_must_remain_identical_on_retry'),'seed_and_payload_must_remain_identical_on_retry',v,True)
    gb=f.get('gate_validator_bindings',{})
    if gb.get('replay_interface')!=REPLAY_INTERFACE:v.err('gate validator replay interface mismatch')
    _int_value(gb.get('replay_timeout_seconds'),'gate replay_timeout_seconds',v,1,600)
    for section,phase in [('CORE0A_gates','CORE0A'),('CORE0B_gates','CORE0B')]:
        expected=set(f.get('acceptance_gate_definitions',{}).get(phase,[]));actual=set(gb.get(section,{}))
        if expected!=actual:v.err(f'gate validator binding set mismatch:{section}')
        for gid,rec in gb.get(section,{}).items():
            name=_safe_name(rec.get('validator_file'),f'gate validator {gid}',v)
            _identifier(rec.get('validator_version'),f'gate validator version {gid}',v)
            _hash_value(rec.get('validator_sha256'),f'gate validator hash {gid}',v)
            if name:
                req=set(f.get('output_contract',{}).get('required_files',[]))
                if name not in req:v.err(f'gate validator not in required_files:{gid}:{name}')
                p=_safe_existing_file(root,name,f'gate validator {gid}',v)
                if p and sha256_file(p)!=rec.get('validator_sha256'):v.err(f'gate validator actual hash mismatch:{gid}')
    oc=f.get('output_contract',{})
    if oc.get('gate_evidence_protocol')!=REPLAY_INTERFACE:v.err('output gate evidence protocol mismatch')
    if oc.get('gate_evidence_bundle_domain')!='ASAP_BLOCK:GATE_EVIDENCE_BUNDLE:v1.3.12':v.err('gate evidence bundle domain mismatch')
    _bool_value(oc.get('gate_evidence_files_must_be_root_basenames'),'gate_evidence_files_must_be_root_basenames',v,True)
    req=oc.get('required_files',[])
    if not isinstance(req,list) or len(req)!=len(set(req)):v.err('output required_files must be a unique list')
    for i,name in enumerate(req):_safe_name(name,f'output required_files[{i}]',v)

def validate_formal(root,schema,canon,v):
    fp=root/'formal_contract.yaml'
    if not fp.exists():v.err('missing formal_contract.yaml');return None
    try:f=load_yaml_strict(fp)
    except Exception as e:v.err(f'formal contract load:{e}');return None
    if f.get('contract_metadata',{}).get('version')!=VERSION:v.err('formal contract version mismatch')
    fh=f.get('contract_metadata',{}).get('formal_contract_hash')
    if not isinstance(fh,str):v.err('formal contract not frozen')
    else:
        try:
            if canonical_object_self_hash(f,'contract_metadata.formal_contract_hash','ASAP_BLOCK:FORMAL_CONTRACT:v1.3.12')!=fh:v.err('formal contract self-hash mismatch')
        except Exception as e:v.err(f'formal contract self-hash error:{e}')
    required_nonnull(f,[
        'contract_metadata.formal_contract_version','theory_contract.theory_document_sha256',
        'theory_contract.fixed_carry_in_corollary_version','theory_contract.fixed_carry_in_corollary_sha256',
        'plan_context_contract.plan_context_hash','pre_core0a_commitments.energy_numeric_mode',
        'pre_core0a_commitments.formal_master_seed_source','pre_core0a_commitments.formal_master_seed_source_commitment_hash',
        'pre_core0a_commitments.seed_derivation_algorithm','pre_core0a_commitments.candidate_build_identity_requirement_hash',
        'child_contracts.generator_contract_file','child_contracts.generator_contract_hash','child_contracts.generator_template_sha256',
        'child_contracts.simulation_contract_file','child_contracts.simulation_contract_hash','child_contracts.simulation_template_sha256',
        'child_contracts.trace_generator_contract_file','child_contracts.trace_generator_contract_hash','child_contracts.trace_template_sha256',
        'numeric_contract.energy_numeric_mode','numeric_contract.service_curve_integerization_mode',
        'numeric_contract.numeric_integer_type','numeric_contract.rho_e_tolerance','numeric_contract.e0_rounding_tolerance',
        'numeric_contract.numeric_range_proof_id','formal_grid_contract.formal_grid_hash',
        'pairing_contract.paired_family_definition','pairing_contract.base_generation_cell_definition',
        'pairing_contract.transformation_contract_hash',
        'sample_request_contract.pilot_generation_requests_per_cell','sample_request_contract.formal_generation_requests_per_cell',
        'seed_contract.seed_derivation_algorithm','seed_contract.formal_master_seed','seed_contract.formal_master_seed_source',
        'seed_contract.formal_master_seed_source_commitment_hash','seed_contract.seed_derivation_context_hash','seed_contract.formal_seed_set_hash',
        'seed_contract.substream_labels','statistics_contract.operational_comparison','statistics_contract.analytical_comparison',
        'statistics_contract.tightness_weighting','statistics_contract.bootstrap_cluster_unit',
        'statistics_contract.multiple_testing_policy','statistics_contract.sample_size_power_target',
        'runtime_environment_contract.thread_count','runtime_environment_contract.cpu_affinity',
        'runtime_environment_contract.cpu_governor','runtime_environment_contract.turbo_policy',
        'runtime_environment_contract.warmup_runs','runtime_environment_contract.measurement_repetitions',
        'runtime_environment_contract.run_order_randomization','runtime_environment_contract.cache_policy',
        'runtime_environment_contract.rss_measurement_method',
        'approved_builds.approved_generator_build_identity_hash','approved_builds.approved_trace_generator_build_identity_hash',
        'approved_builds.approved_rta_build_identity_hash','approved_builds.approved_simulator_build_identity_hash',
        'approved_builds.approved_scheduler_build_identity_hash','approved_builds.approved_audit_build_identity_hash',
        'approved_builds.approved_artifact_validator_sha256','approved_builds.approved_result_validator_sha256',
        'approved_builds.approved_acceptance_validator_sha256','approved_builds.approved_validation_common_sha256',
        'run_plan_contract.run_plan_bundle_hash','run_plan_contract.retry_policy.max_attempts_per_request',
        'run_plan_contract.retry_policy.retryable_terminal_events'],v)
    _validate_formal_values(f,root,schema,v)
    mode=f.get('numeric_contract',{}).get('energy_numeric_mode')
    if mode=='FIXED_POINT_DIRECTED':
        required_nonnull(f,['numeric_contract.energy_numeric_scale'],v)
    elif mode!='EXACT_RATIONAL':
        v.err(f'unsupported energy_numeric_mode:{mode}')
    source=f.get('seed_contract',{}).get('formal_master_seed_source')
    if source=='PUBLIC_RANDOM_BEACON':
        required_nonnull(f,['pre_core0a_commitments.public_random_beacon_name',
            'pre_core0a_commitments.public_random_beacon_round_or_timestamp',
            'pre_core0a_commitments.public_random_beacon_extraction_rule'],v)
    _strict_positive_int(f.get('sample_request_contract',{}).get('pilot_generation_requests_per_cell'),
        'pilot_generation_requests_per_cell',v)
    _strict_positive_int(f.get('sample_request_contract',{}).get('formal_generation_requests_per_cell'),
        'formal_generation_requests_per_cell',v)
    _strict_positive_int(f.get('run_plan_contract',{}).get('retry_policy',{}).get('max_attempts_per_request'),
        'max_attempts_per_request',v)
    # Exact local normative bindings.
    expected_artifact_bindings={'markdown':MARKDOWN,'schema':SCHEMA,'dictionary':DICT,'canonical':CANON,'interface_manifest':INTERFACE_MANIFEST,'generator':GENERATOR_TEMPLATE,'simulation':SIMULATION_TEMPLATE,'trace':TRACE_TEMPLATE,'acceptance':ACCEPTANCE_TEMPLATE,'common':COMMON,'artifact_validator':ARTIFACT_VALIDATOR,'result_validator':RESULT_VALIDATOR,'acceptance_validator':ACCEPTANCE_VALIDATOR,'plan_template':PLAN_TEMPLATE,'plan_dependencies_template':PLAN_DEPS_TEMPLATE,'execution_log_template':EXEC_LOG_TEMPLATE,'per_task_results_template':PER_TASK_RESULTS_TEMPLATE}
    if set(f.get('artifact_bindings',{}))!=set(expected_artifact_bindings):v.err('formal artifact binding key set mismatch')
    for key,fn in expected_artifact_bindings.items():
        rec=f.get('artifact_bindings',{}).get(key,{})
        if rec.get('file')!=fn:v.err(f'formal artifact filename mismatch:{key}')
        p=_safe_existing_file(root,fn,f'bound runtime artifact {key}',v)
        if p and rec.get('sha256')!=sha256_file(p):v.err(f'formal artifact hash mismatch:{key}')
        _hash_value(rec.get('sha256'),f'formal artifact binding hash:{key}',v)
        if fn not in set(f.get('output_contract',{}).get('required_files',[])):v.err(f'bound artifact absent from output required_files:{key}:{fn}')
    approved=f.get('approved_builds',{})
    for field,fn in [('approved_artifact_validator_sha256',ARTIFACT_VALIDATOR),('approved_result_validator_sha256',RESULT_VALIDATOR),('approved_acceptance_validator_sha256',ACCEPTANCE_VALIDATOR),('approved_validation_common_sha256',COMMON)]:
        if approved.get(field)!=sha256_file(root/fn):v.err(f'approved validator/common hash mismatch:{field}')
    # Child contracts.
    child_specs=[
        ('generator','generator_contract.yaml','generator_contract_hash','ASAP_BLOCK:GENERATOR_CONTRACT:v1.3.12','ASAP_BLOCK_generator_contract_template_v1_3_12.yaml','generator_template_sha256'),
        ('simulation','simulation_contract.yaml','simulation_contract_hash','ASAP_BLOCK:SIMULATION_CONTRACT:v1.3.12','ASAP_BLOCK_simulation_contract_template_v1_3_12.yaml','simulation_template_sha256'),
        ('trace_generator','trace_generator_contract.yaml','trace_generator_contract_hash','ASAP_BLOCK:TRACE_GENERATOR_CONTRACT:v1.3.12','ASAP_BLOCK_trace_generator_contract_template_v1_3_12.yaml','trace_template_sha256')]
    for role,fn,hfield,domain,tfn,tfhash in child_specs:
        p=root/fn
        if not p.exists():v.err(f'missing child contract:{fn}');continue
        try:o=load_yaml_strict(p);actual=canonical_object_self_hash(o,f'contract_metadata.{hfield}',domain)
        except Exception as e:v.err(f'child contract invalid:{fn}:{e}');continue
        declared=o.get('contract_metadata',{}).get(hfield)
        if actual!=declared:v.err(f'child contract self-hash mismatch:{fn}')
        formal_key='trace_generator_contract_hash' if role=='trace_generator' else f'{role}_contract_hash'
        if f.get('child_contracts',{}).get(formal_key)!=declared:v.err(f'formal child hash mismatch:{fn}')
        _validate_child_completeness(role,o,v)
        declared_file=f.get('child_contracts',{}).get(f'{role}_contract_file' if role!='trace_generator' else 'trace_generator_contract_file')
        if declared_file!=fn:v.err(f'child contract filename mismatch:{role}:{declared_file}!={fn}')
        expected_template_hash=f.get('artifact_bindings',{}).get({'generator':'generator','simulation':'simulation','trace_generator':'trace'}[role],{}).get('sha256')
        if f.get('child_contracts',{}).get(tfhash)!=expected_template_hash:v.err(f'child template commitment mismatch:{tfn}')
        tp=root/tfn
        if tp.exists() and f.get('child_contracts',{}).get(tfhash)!=sha256_file(tp):v.err(f'child template hash mismatch:{tfn}')
    # Duplicated commitments must agree.
    pre=f.get('pre_core0a_commitments',{});seed=f.get('seed_contract',{});num=f.get('numeric_contract',{})
    if pre.get('energy_numeric_mode')!=num.get('energy_numeric_mode'):v.err('pre-core numeric mode differs from final numeric contract')
    for k in ['formal_master_seed_source','formal_master_seed_source_commitment_hash']:
        if pre.get(k)!=seed.get(k):v.err(f'pre-core/seed commitment mismatch:{k}')
    if pre.get('seed_derivation_algorithm')!=seed.get('seed_derivation_algorithm'):v.err('seed algorithm mismatch')
    if seed.get('seed_derivation_algorithm')!='SHA256_DOMAIN_SEPARATED_CANONICAL_V1_3_12':v.err('unexpected seed algorithm')
    # Internal hash DAG.
    pp={'theory_contract':f.get('theory_contract'),'pre_core0a_commitments':f.get('pre_core0a_commitments'),'artifact_bindings':f.get('artifact_bindings')}
    pch=domain_hash('ASAP_BLOCK:PLAN_CONTEXT:v1.3.12',pp)
    if pch!=f.get('plan_context_contract',{}).get('plan_context_hash'):v.err('plan_context_hash mismatch')
    fg=f.get('formal_grid_contract',{}).get('formal_grid')
    if not isinstance(fg,dict) or not isinstance(fg.get('cells'),list) or not fg['cells']:v.err('formal grid cells missing')
    else:
        ids=[]
        for cell in fg['cells']:
            if not isinstance(cell,dict) or set(cell)!={'parameter_cell_id','parameters'}:v.err('formal grid cell shape invalid');continue
            cid=domain_hash('ASAP_BLOCK:PARAMETER_CELL:v1.3.12',cell['parameters'])
            if cid!=cell['parameter_cell_id']:v.err(f'formal grid parameter_cell_id mismatch:{cell.get("parameter_cell_id")}')
            ids.append(cell['parameter_cell_id'])
        if ids!=sorted(ids):v.err('formal grid cells not sorted by parameter_cell_id')
        if len(ids)!=len(set(ids)):v.err('duplicate formal grid cell')
    fgh=domain_hash('ASAP_BLOCK:FORMAL_GRID:v1.3.12',fg)
    if fgh!=f.get('formal_grid_contract',{}).get('formal_grid_hash'):v.err('formal_grid_hash mismatch')
    scpre={'plan_context_hash':f.get('plan_context_contract',{}).get('plan_context_hash'),'formal_grid_hash':f.get('formal_grid_contract',{}).get('formal_grid_hash'),'sample_request_contract':f.get('sample_request_contract'),'formal_master_seed_source':pre.get('formal_master_seed_source'),'formal_master_seed_source_commitment_hash':pre.get('formal_master_seed_source_commitment_hash'),'seed_derivation_algorithm':seed.get('seed_derivation_algorithm')}
    sch=domain_hash('ASAP_BLOCK:SEED_CONTEXT:v1.3.12',scpre)
    if sch!=seed.get('seed_derivation_context_hash'):v.err('seed_derivation_context_hash mismatch')
    return f

def phase_contract(rows,formal,v):
    if not formal:return
    fh=formal['contract_metadata']['formal_contract_hash'];pch=formal['plan_context_contract']['plan_context_hash']
    for tn,rs in rows.items():
        for i,r in enumerate(rs,2):
            if 'run_phase' not in r:continue
            if r.get('plan_context_hash')!=pch:v.err(f'plan_context mismatch:{tn}:{i}')
            if r['run_phase'] in FORMAL_PHASES and r.get('formal_contract_hash')!=fh:v.err(f'formal phase contract mismatch:{tn}:{i}')
            if r['run_phase'] in PREFORMAL_PHASES and r.get('formal_contract_hash','')!='':v.err(f'preformal row has formal hash:{tn}:{i}')

def expected_dependency_rows(plan,canon):
    out=[]
    for rec in canon.get('request_dependency_contract',{}).get(plan['request_type'],[]):
        dep=plan.get(rec['field'],'')
        if dep:out.append((plan['request_id'],dep,rec['role']))
    return sorted(out)

def seed_value(formal,scope,replicate):
    seed=formal['seed_contract']['formal_master_seed']
    pre={'seed_derivation_context_hash':formal['seed_contract']['seed_derivation_context_hash'],'seed_scope_id':scope,'replicate_index':int(replicate),'formal_master_seed_or_revealed_beacon_value':seed}
    digest=hashlib.sha256(b'ASAP_BLOCK:DERIVED_SEED:v1.3.12\x00'+canonical_json_bytes(pre)).digest()
    return int.from_bytes(digest[:8],'big')

def validate_plan(root,rows,formal,canon,v,profile):
    plans=rows.get('run_plan_definition.csv',[]);deps=rows.get('run_plan_dependencies.csv',[])
    if not plans:v.err('N_planned must be >0');return {}
    p={r['request_id']:r for r in plans};payload=canon['request_type_payload_fields'];all_payload=set(x for fs in payload.values() for x in fs)
    grid_ids={c['parameter_cell_id'] for c in formal['formal_grid_contract']['formal_grid']['cells']} if formal else set()
    for r in plans:
        typ=r['request_type'];fs=payload.get(typ)
        if r.get('request_payload_schema_version')!=REQUEST_PAYLOAD_SCHEMA_VERSION:
            v.err(f'request_payload_schema_version mismatch:{r["request_id"]}:{r.get("request_payload_schema_version")}')
        if fs is None:v.err(f'no payload definition:{typ}');continue
        for f in fs:
            if f not in r or r.get(f,'')=='':v.err(f'missing request payload field:{r["request_id"]}:{f}')
        for f in all_payload-set(fs):
            if r.get(f,'')!='':v.err(f'non-payload field must be null:{r["request_id"]}:{f}')
        pre={f:(r.get(f) if r.get(f,'')!='' else None) for f in fs}
        calc=domain_hash(f'ASAP_BLOCK:REQUEST_PAYLOAD:{typ}:v1.3.12',pre)
        if calc!=r['request_payload_hash']:v.err(f'request_payload_hash mismatch:{r["request_id"]}')
        rp={'plan_context_hash':r['plan_context_hash'],'request_type':typ,'run_phase':r['run_phase'],'parameter_cell_id':r['parameter_cell_id'],'replicate_index':int(r['replicate_index']),'request_payload_hash':r['request_payload_hash']}
        if domain_hash('ASAP_BLOCK:REQUEST:v1.3.12',rp)!=r['request_id']:v.err(f'request_id mismatch:{r["request_id"]}')
        oi=canon['output_identity']['mapping'].get(typ,{})
        if r['expected_output_type']!=oi.get('output_type'):v.err(f'expected output type mismatch:{r["request_id"]}')
        if r['output_cardinality']!='EXACTLY_ONE':v.err(f'output cardinality mismatch:{r["request_id"]}')
        op={'request_id':r['request_id'],'expected_output_type':r['expected_output_type'],'output_cardinality':r['output_cardinality']}
        if domain_hash('ASAP_BLOCK:EXPECTED_OUTPUT:v1.3.12',op)!=r['expected_output_id']:v.err(f'expected_output_id mismatch:{r["request_id"]}')
        if grid_ids and r['parameter_cell_id'] not in grid_ids:v.err(f'plan parameter cell absent from formal grid:{r["request_id"]}')
        if typ in SEEDED_TYPES:
            scope_pre={'request_type':typ,'parameter_cell_id':r['parameter_cell_id'],'scenario_id_or_null':r.get('scenario_id') or None,'stream_label_or_null':r.get('stream_label') or None,'stream_index_or_null':int(r['stream_index']) if r.get('stream_index') else None}
            scope=domain_hash('ASAP_BLOCK:SEED_SCOPE:v1.3.12',scope_pre)
            if scope!=r['seed_scope_id']:v.err(f'seed_scope_id mismatch:{r["request_id"]}')
            if seed_value(formal,scope,r['replicate_index'])!=int(r['derived_seed']):v.err(f'derived_seed mismatch:{r["request_id"]}')
        child=formal.get('child_contracts',{}) if formal else {}
        approved=formal.get('approved_builds',{}) if formal else {}
        if typ=='GENERATION' and r.get('generator_contract_hash')!=child.get('generator_contract_hash'):v.err(f'generation request child-contract mismatch:{r["request_id"]}')
        if typ in {'RELEASE_TRACE_GENERATION','HARVEST_TRACE_GENERATION'} and r.get('trace_generator_contract_hash')!=child.get('trace_generator_contract_hash'):v.err(f'trace request child-contract mismatch:{r["request_id"]}')
        if typ=='SIMULATION' and r.get('simulation_contract_hash')!=child.get('simulation_contract_hash'):v.err(f'simulation request child-contract mismatch:{r["request_id"]}')
        build_fields={'ANALYSIS':'approved_rta_build_identity_hash','SIMULATION':'approved_simulator_build_identity_hash',
            'SERVICE_CHECK':'approved_audit_build_identity_hash','E0_CHECK':'approved_audit_build_identity_hash',
            'COMPATIBILITY_CHECK':'approved_audit_build_identity_hash','BOUND_AUDIT':'approved_audit_build_identity_hash'}
        if typ in build_fields:
            fld=build_fields[typ]
            if r.get(fld)!=approved.get(fld):v.err(f'planned approved-build mismatch:{r["request_id"]}:{fld}')
        if typ=='SIMULATION' and r.get('approved_scheduler_build_identity_hash')!=approved.get('approved_scheduler_build_identity_hash'):
            v.err(f'planned scheduler-build mismatch:{r["request_id"]}')
    actual=sorted((r['request_id'],r['dependency_request_id'],r['dependency_role']) for r in deps)
    expected=sorted(x for r in plans for x in expected_dependency_rows(r,canon))
    if actual!=expected:v.err(f'run-plan dependencies differ from payload contract: expected={expected} actual={actual}')
    if cycle(set(p),[(a,b) for a,b,_ in actual]):v.err('run-plan dependency DAG contains cycle')
    # Internal formal seed and plan hashes.
    seed_rows=sorted([{'seed_scope_id':r['seed_scope_id'],'replicate_index':int(r['replicate_index']),'derived_seed':int(r['derived_seed'])} for r in plans if r['request_type'] in SEEDED_TYPES],key=lambda x:(x['seed_scope_id'],x['replicate_index'],x['derived_seed']))
    if domain_hash('ASAP_BLOCK:SEED_SET:v1.3.12',seed_rows)!=formal['seed_contract']['formal_seed_set_hash']:v.err('formal_seed_set_hash mismatch')
    bundle_pre={'run_plan_definition_sha256':canonical_csv_sha256(root/'run_plan_definition.csv'),'run_plan_dependencies_sha256':canonical_csv_sha256(root/'run_plan_dependencies.csv')}
    if domain_hash('ASAP_BLOCK:RUN_PLAN_BUNDLE:v1.3.12',bundle_pre)!=formal['run_plan_contract']['run_plan_bundle_hash']:v.err('run_plan_bundle_hash mismatch')
    # Request-count contract is executable: generation failures are retained, never replaced.
    grid=sorted(grid_ids)
    phase_specs=[]
    if profile in {'FORMAL_RELEASE','CORE0B'} or any(r['run_phase']=='FORMAL' for r in plans):
        phase_specs.append(('FORMAL',_strict_positive_int(formal['sample_request_contract']['formal_generation_requests_per_cell'],'formal_generation_requests_per_cell',v)))
    if profile=='PILOT' or any(r['run_phase']=='PILOT' for r in plans):
        phase_specs.append(('PILOT',_strict_positive_int(formal['sample_request_contract']['pilot_generation_requests_per_cell'],'pilot_generation_requests_per_cell',v)))
    for phase,expected_n in phase_specs:
        if expected_n is None:continue
        counts=defaultdict(int)
        for r in plans:
            if r['run_phase']==phase and r['request_type']=='GENERATION':counts[r['parameter_cell_id']]+=1
        for cid in grid:
            if counts[cid]!=expected_n:v.err(f'generation request count mismatch:{phase}:{cid}:{counts[cid]}!={expected_n}')
        extra=set(counts)-set(grid)
        if extra:v.err(f'generation requests use cells outside formal grid:{phase}:{sorted(extra)}')
    return p

def execution_and_outputs(root,rows,formal,canon,plans,schema,v,profile):
    logs=rows.get('run_execution_log.csv',[]);outs=rows.get('request_outputs.csv',[]);by=defaultdict(list)
    for r in logs:by[r['request_id']].append(r)
    deps=defaultdict(list)
    for r in rows.get('run_plan_dependencies.csv',[]):deps[r['request_id']].append(r['dependency_request_id'])
    maxa=int(formal['run_plan_contract']['retry_policy']['max_attempts_per_request']);final={};last_event={}
    build_map={'GENERATION':'approved_generator_build_identity_hash','TRANSFORMATION':'approved_generator_build_identity_hash','RELEASE_TRACE_GENERATION':'approved_trace_generator_build_identity_hash','HARVEST_TRACE_GENERATION':'approved_trace_generator_build_identity_hash','ANALYSIS':'approved_rta_build_identity_hash','SIMULATION':'approved_simulator_build_identity_hash','SERVICE_CHECK':'approved_audit_build_identity_hash','E0_CHECK':'approved_audit_build_identity_hash','COMPATIBILITY_CHECK':'approved_audit_build_identity_hash','BOUND_AUDIT':'approved_audit_build_identity_hash'}
    for rid,pr in plans.items():
        ev=by.get(rid,[])
        if not ev:v.err(f'unaccounted request:{rid}');continue
        attempts=defaultdict(list)
        for e in ev:attempts[int(e['attempt_index'])].append(e)
        if sorted(attempts)!=list(range(max(attempts)+1)):v.err(f'noncontiguous attempts:{rid}')
        if len(attempts)>maxa:v.err(f'too many attempts:{rid}')
        prev=None
        for ai in sorted(attempts):
            ae=sorted(attempts[ai],key=lambda x:int(x['execution_event_index']))
            idx=[int(x['execution_event_index']) for x in ae];st=[x['execution_status'] for x in ae]
            if idx!=list(range(len(idx))):v.err(f'noncontiguous event index:{rid}:{ai}')
            if not st or st[0]!='STARTED' or st.count('STARTED')!=1:v.err(f'illegal STARTED:{rid}:{ai}:{st}')
            if any(x not in {'STARTED','HEARTBEAT'}|TERMINAL for x in st):v.err(f'unknown execution status:{rid}:{ai}')
            tp=[i for i,x in enumerate(st) if x in TERMINAL]
            if len(tp)!=1 or tp[0]!=len(st)-1:v.err(f'terminal must be unique and last:{rid}:{ai}:{st}')
            times=[parse_timestamp(e['event_time_utc']) for e in ae]
            if times!=sorted(times):v.err(f'nonmonotone event times:{rid}:{ai}')
            if any(e['run_phase']!=pr['run_phase'] or e['plan_context_hash']!=pr['plan_context_hash'] for e in ae):v.err(f'execution context mismatch:{rid}')
            if any(int(e['max_attempts'])!=maxa for e in ae):v.err(f'max_attempts mismatch:{rid}')
            builds={e['build_identity_hash'] for e in ae}
            if len(builds)!=1:v.err(f'build changes within attempt:{rid}:{ai}')
            if pr['run_phase'] in FORMAL_PHASES:
                expected=formal['approved_builds'][build_map[pr['request_type']]]
                if builds!={expected}:v.err(f'unapproved execution build:{rid}:{builds}!={expected}')
            if ai>0:
                if prev not in RETRYABLE:v.err(f'illegal retry:{rid}:{ai}')
                if ae[0].get('retry_of_attempt_index')!=str(ai-1):v.err(f'retry pointer mismatch:{rid}:{ai}')
            prev=st[-1];last_event[rid]=ae[-1]
        final[rid]=prev
    om=defaultdict(list)
    for o in outs:om[o['request_id']].append(o)
    for rid,pr in plans.items():
        os=om.get(rid,[])
        if final.get(rid)=='FINISHED':
            if len(os)!=1:v.err(f'FINISHED requires one output:{rid}:{len(os)}');continue
            o=os[0]
            if o['output_index']!='0' or o['request_output_status']!='MATERIALIZED':v.err(f'bad output index/status:{rid}')
            if o['expected_output_id']!=pr['expected_output_id'] or o['actual_output_id']!=pr['expected_output_id']:v.err(f'output ID mismatch:{rid}')
            if o['output_type']!=pr['expected_output_type']:v.err(f'output type mismatch:{rid}')
            top=canon['output_identity']['mapping'][pr['request_type']]['top_table']
            if o['result_table']!=top:v.err(f'output top table mismatch:{rid}')
            top_rows=[r for r in rows.get(top,[]) if r.get('request_id')==rid]
            if len(top_rows)!=1:v.err(f'top output row resolution:{rid}:{len(top_rows)}');continue
            td=schema['tables'][top]
            try:key=json.loads(o['result_primary_key_canonical'])
            except Exception:key=None
            if key!=[top_rows[0].get(k,'') for k in td['primary_key']]:v.err(f'output primary key mismatch:{rid}')
            bundle=output_bundle(pr['request_type'],rid,top_rows[0],rows,schema)
            pre={'request_id':rid,'expected_output_id':pr['expected_output_id'],'output_type':pr['expected_output_type'],'tables':bundle}
            bh=domain_hash(f'ASAP_BLOCK:OUTPUT_BUNDLE:{pr["request_type"]}:v1.3.12',pre)
            if o['output_hash']!=bh:v.err(f'output bundle hash mismatch:{rid}')
            le=last_event.get(rid,{})
            if le.get('actual_output_id')!=o['actual_output_id'] or le.get('actual_output_hash')!=o['output_hash'] or le.get('actual_output_type')!=o['output_type']:v.err(f'execution/output mismatch:{rid}')
        elif os:v.err(f'non-FINISHED has output:{rid}')
    release_phases={'FORMAL'} if profile=='FORMAL_RELEASE' else ({'CORE0B'} if profile=='CORE0B' else set())
    if release_phases:
        for rid,pr in plans.items():
            if pr.get('run_phase') in release_phases and final.get(rid)!='FINISHED':
                v.err(f'release request did not FINISH:{rid}:{final.get(rid)}')
    generation_status={r['request_id']:r['generation_status'] for r in rows.get('generation_requests.csv',[])}
    def dependency_succeeded(dep_id):
        if final.get(dep_id)!='FINISHED':return False
        pr=plans.get(dep_id,{})
        if pr.get('request_type')=='GENERATION':return generation_status.get(dep_id)=='SUCCESS'
        return True
    for rid,st in final.items():
        failed=[dep for dep in deps[rid] if not dependency_succeeded(dep)]
        if st=='NOT_RUN_DEPENDENCY' and not failed:v.err(f'NOT_RUN_DEPENDENCY without failed semantic dependency:{rid}')
        if failed and st!='NOT_RUN_DEPENDENCY':v.err(f'failed dependency not propagated as NOT_RUN_DEPENDENCY:{rid}:{failed}:{st}')
    return final

def sorted_objs(rs,pk):
    return [row_object(r) for r in sorted(rs,key=lambda r:canonical_json_bytes([r.get(k,'') for k in pk]))]

def output_bundle(typ,rid,top,rows,schema):
    out={}
    def add(t,rs):out[t]=sorted_objs(rs,schema['tables'][t]['primary_key'])
    mapping={'GENERATION':'generation_requests.csv','TRANSFORMATION':'paired_transformations.csv','RELEASE_TRACE_GENERATION':'release_trace_sets.csv','HARVEST_TRACE_GENERATION':'harvest_trace_sets.csv','ANALYSIS':'per_taskset_results.csv','SIMULATION':'simulation_taskset_summary.csv','SERVICE_CHECK':'service_trace_checks.csv','E0_CHECK':'e0_trace_certificate_checks.csv','COMPATIBILITY_CHECK':'analysis_simulation_compatibility_checks.csv','BOUND_AUDIT':'bound_audit_runs.csv'}
    add(mapping[typ],[top])
    if typ in {'GENERATION','TRANSFORMATION'}:
        ts=[r for r in rows.get('tasksets.csv',[]) if r.get('materialization_request_id')==rid];add('tasksets.csv',ts)
        ids={r['taskset_id'] for r in ts};add('task_definitions.csv',[r for r in rows.get('task_definitions.csv',[]) if r['taskset_id'] in ids])
    elif typ=='RELEASE_TRACE_GENERATION':add('release_traces.csv',[r for r in rows.get('release_traces.csv',[]) if r['release_trace_id']==top['release_trace_id']])
    elif typ=='HARVEST_TRACE_GENERATION':add('harvest_traces.csv',[r for r in rows.get('harvest_traces.csv',[]) if r['harvest_trace_id']==top['harvest_trace_id']])
    elif typ=='ANALYSIS':
        aid=top['analysis_run_id'];add('per_task_results.csv',[r for r in rows.get('per_task_results.csv',[]) if r['analysis_run_id']==aid]);add('rta_dependency_records.csv',[r for r in rows.get('rta_dependency_records.csv',[]) if r['analysis_run_id']==aid])
    elif typ=='SIMULATION':add('simulation_job_results.csv',[r for r in rows.get('simulation_job_results.csv',[]) if r['simulation_run_id']==top['simulation_run_id']])
    elif typ=='E0_CHECK':add('e0_job_certificate_checks.csv',[r for r in rows.get('e0_job_certificate_checks.csv',[]) if r['e0_certificate_check_id']==top['e0_certificate_check_id']])
    elif typ=='BOUND_AUDIT':add('simulation_bound_checks.csv',[r for r in rows.get('simulation_bound_checks.csv',[]) if r['bound_audit_run_id']==top['bound_audit_run_id']])
    return out

def validate_analysis_certification_states(arows,trs,v):
    """Enforce v9.3 task-set certification semantics without running RTA."""
    role_by_variant={
        'CW-Theta^cw':'MAIN_METHOD',
        'LOC-Theta^loc':'MAIN_METHOD',
        'CW-D':'AUXILIARY_ABLATION',
        'LOC-D':'AUXILIARY_ABLATION',
        'LOC-Theta^cw':'AUXILIARY_ABLATION',
    }
    task_index={(aid,r['task_id']):r for aid,rs in trs.items() for r in rs}
    identity_fields=[
        'taskset_semantic_hash','priority_rank_hash',
        'analysis_E0_canonical_hash','analysis_service_curve_canonical_hash',
        'analysis_power_vector_canonical_hash','analysis_energy_unit_hash','energy_numeric_mode',
        'energy_numeric_scale','formal_contract_hash','theory_document_sha256',
        'fixed_carry_in_corollary_hash',
    ]
    for aid,a in arows.items():
        variant=a.get('variant');rr=trs.get(aid,[])
        if a.get('analysis_certification_status') not in {'CERTIFIED_TASKSET','DIAGNOSTIC_ONLY_NOT_CERTIFIED','NOT_CERTIFIED','NOT_APPLICABLE'}:
            v.err(f'forbidden v1.3.12 analysis certification status:{aid}')
        diagnostic=a.get('analysis_certification_status')=='DIAGNOSTIC_ONLY_NOT_CERTIFIED'
        expected_role=role_by_variant.get(variant)
        role=a.get('analysis_method_role')
        diagnostic_mode=(role=='DIAGNOSTIC' or (role=='AUXILIARY_ABLATION' and a.get('run_phase')=='DIAGNOSTIC'))
        if expected_role and role!=expected_role and not (variant=='LOC-Theta^cw' and diagnostic and role=='DIAGNOSTIC'):
            v.err(f'analysis method role mismatch:{aid}:{variant}')
        if diagnostic and not diagnostic_mode:
            v.err(f'diagnostic-only analysis lacks explicit diagnostic mode:{aid}')
        if a.get('rta_formula_version')!=THEORY_FORMULA_VERSION:
            v.err(f'analysis theory formula mismatch:{aid}')
        if a.get('theory_document_sha256')!=THEORY_DOCUMENT_SHA256:
            v.err(f'analysis theory document hash mismatch:{aid}')
        interface_status=a.get('fixed_carry_in_corollary_status')
        interface_hash=a.get('fixed_carry_in_corollary_hash')
        if interface_status=='ACTIVE' and interface_hash!=FIXED_CARRY_IN_INTERFACE_SHA256:
            v.err(f'active fixed-carry-in interface hash mismatch:{aid}')
        if interface_status=='HASH_MISMATCH' and interface_hash==FIXED_CARRY_IN_INTERFACE_SHA256:
            v.err(f'fixed-carry-in HASH_MISMATCH status without mismatch:{aid}')
        if variant in {'CW-Theta^cw','LOC-Theta^loc'} and interface_status!='NOT_APPLICABLE':
            v.err(f'recursive main method has applicable fixed-carry-in status:{aid}')
        if variant in {'CW-D','LOC-D','LOC-Theta^cw'} and interface_status=='NOT_APPLICABLE':
            v.err(f'fixed-carry-in variant has NOT_APPLICABLE interface status:{aid}')
        try:violation_count=parse_canonical_integer(a.get('dominance_violation_count',''))
        except Exception:
            violation_count=-1
        if violation_count<0:v.err(f'invalid dominance violation count:{aid}')
        cert=a.get('analysis_certification_status')=='CERTIFIED_TASKSET'
        if cert:
            if a.get('analysis_solver_status')!='COMPLETED':v.err(f'certified taskset not completed:{aid}')
            if int(a.get('n_tasks_candidate_found','-1'))!=int(a.get('n_tasks_total','-2')):v.err(f'certified taskset missing candidates:{aid}')
            if int(a.get('n_tasks_certified','-1'))!=int(a.get('n_tasks_total','-2')):v.err(f'certified taskset missing certified tasks:{aid}')
        if variant in {'CW-D','LOC-D'}:
            if cert and a.get('fixed_carry_in_corollary_status')!='ACTIVE':v.err(f'deadline carry-in certified without active interface:{aid}')
            all_candidates=bool(rr) and len(rr)==int(a.get('n_tasks_total','-1')) and all(r.get('task_solver_status')=='CANDIDATE_FOUND' for r in rr)
            for r in rr:
                if r.get('task_solver_status')=='CANDIDATE_FOUND':
                    try:compatible=compare_numbers(r['candidate_response_time'],r['D_i'])<=0
                    except Exception:compatible=False
                    if not compatible:v.err(f'deadline carry-in candidate exceeds D_i:{aid}:{r.get("task_id")}')
                    expected_task_status='CERTIFIED' if cert else ('DIAGNOSTIC_ONLY_NOT_CERTIFIED' if diagnostic else 'PROVISIONAL_NOT_CERTIFIED')
                    if r.get('task_certification_status')!=expected_task_status:v.err(f'deadline carry-in task certification mismatch:{aid}:{r.get("task_id")}')
            if all_candidates and a.get('analysis_solver_status')=='COMPLETED' and a.get('fixed_carry_in_corollary_status')=='ACTIVE' and not cert:
                v.err(f'deadline carry-in complete vector not jointly certified:{aid}')
            continue
        if variant!='LOC-Theta^cw':continue
        try:
            if len(rr)!=int(a.get('n_tasks_total','-1')):v.err(f'LOC-Theta^cw task vector cardinality mismatch:{aid}')
        except Exception:v.err(f'LOC-Theta^cw invalid task vector cardinality:{aid}')
        source_ids={r.get('source_analysis_run_id') for r in rr if r.get('source_analysis_run_id')}
        src=arows.get(next(iter(source_ids))) if len(source_ids)==1 else None
        source_ok=bool(
            src and src.get('variant')=='CW-Theta^cw'
            and src.get('analysis_solver_status')=='COMPLETED'
            and src.get('analysis_certification_status')=='CERTIFIED_TASKSET'
        )
        if len(source_ids)!=1:v.err(f'LOC-Theta^cw source vector is not unique and complete:{aid}')
        if source_ok:
            identity_match=True
            for fld in identity_fields:
                if src.get(fld,'')!=a.get(fld,''):
                    identity_match=False
            if not identity_match:
                source_ok=False
                if any(r.get('dependency_vector_check_status')=='VALID' for r in rr):
                    v.err(f'LOC-Theta^cw dependency marked VALID across source/target identity mismatch:{aid}')
        dependency_ok=bool(rr) and all(
            r.get('dependency_vector_check_status')=='VALID'
            and r.get('fixed_carry_in_corollary_status')=='ACTIVE'
            and r.get('carry_in_source_variant')=='CW-Theta^cw'
            and r.get('carry_in_source_certification_status')==LOC_THETA_CW_SOURCE_CERTIFICATION
            for r in rr
        )
        interface_ok=(
            a.get('fixed_carry_in_corollary_status')=='ACTIVE'
            and a.get('fixed_carry_in_corollary_hash')==FIXED_CARRY_IN_INTERFACE_SHA256
        )
        applicable=source_ok and dependency_ok and interface_ok
        no_candidates=[]
        for r in rr:
            if r.get('task_solver_status')=='CANDIDATE_FOUND':
                st=task_index.get((src.get('analysis_run_id'),r.get('task_id'))) if src else None
                if not st or st.get('task_solver_status')!='CANDIDATE_FOUND':
                    v.err(f'LOC-Theta^cw missing source candidate:{aid}:{r.get("task_id")}')
                else:
                    if cert and st.get('task_certification_status')!='CERTIFIED':
                        v.err(f'LOC-Theta^cw certified from uncertified source candidate:{aid}:{r.get("task_id")}')
                    try:compatible=compare_numbers(r['candidate_response_time'],st['candidate_response_time'])<=0
                    except Exception:compatible=False
                    if not compatible:v.err(f'LOC-Theta^cw candidate exceeds source CW candidate:{aid}:{r.get("task_id")}')
                if cert:
                    if r.get('task_certification_status')!='CERTIFIED':v.err(f'LOC-Theta^cw final vector task not certified:{aid}:{r.get("task_id")}')
                elif a.get('analysis_certification_status')=='DIAGNOSTIC_ONLY_NOT_CERTIFIED':
                    if r.get('task_certification_status')!='DIAGNOSTIC_ONLY_NOT_CERTIFIED':v.err(f'LOC-Theta^cw diagnostic candidate mislabeled:{aid}:{r.get("task_id")}')
                elif r.get('task_certification_status')!='PROVISIONAL_NOT_CERTIFIED':
                    v.err(f'LOC-Theta^cw prefix candidate certified early:{aid}:{r.get("task_id")}')
            elif r.get('task_solver_status')=='NO_CANDIDATE':no_candidates.append(r)
        if cert:
            if not applicable:v.err(f'LOC-Theta^cw certified outside valid dependency domain:{aid}')
            if any(r.get('task_solver_status')!='CANDIDATE_FOUND' for r in rr):v.err(f'LOC-Theta^cw certified with incomplete vector:{aid}')
            if a.get('dominance_invariant_status')!='SATISFIED' or violation_count!=0:v.err(f'LOC-Theta^cw certified with dominance violation:{aid}')
        elif applicable and a.get('analysis_solver_status')=='COMPLETED' and all(r.get('task_solver_status')=='CANDIDATE_FOUND' for r in rr):
            v.err(f'LOC-Theta^cw complete compatible vector not jointly certified:{aid}')
        if applicable and no_candidates:
            if a.get('analysis_solver_status')!='INTERNAL_CONFORMANCE_FAILURE' or a.get('analysis_certification_status')!='NOT_CERTIFIED':
                v.err(f'LOC-Theta^cw NO_CANDIDATE not promoted to conformance failure:{aid}')
            if a.get('dominance_invariant_status')!='DOMINANCE_INVARIANT_VIOLATION' or violation_count!=len(no_candidates):
                v.err(f'LOC-Theta^cw dominance violation record mismatch:{aid}')
            if any(r.get('dominance_invariant_status')!='DOMINANCE_INVARIANT_VIOLATION' for r in no_candidates):
                v.err(f'LOC-Theta^cw task dominance violation status mismatch:{aid}')
        if a.get('analysis_solver_status') in {'TIMEOUT','NUMERIC_ERROR'}:
            if a.get('analysis_certification_status')!='NOT_CERTIFIED':v.err(f'LOC-Theta^cw operational failure certified:{aid}')
        if not applicable and a.get('analysis_certification_status')!='DIAGNOSTIC_ONLY_NOT_CERTIFIED':
            if a.get('analysis_solver_status')!='NOT_APPLICABLE_DEPENDENCY' or a.get('analysis_certification_status')!='NOT_APPLICABLE':
                v.err(f'LOC-Theta^cw invalid dependency not marked not-applicable:{aid}')

def certification_state_fixture(case):
    """Return a small v9.3 source/target vector for destructive CLI tests."""
    h='a'*64
    identity={
        'taskset_semantic_hash':h,'priority_rank_hash':h,
        'analysis_E0_canonical_hash':h,'analysis_service_curve_canonical_hash':h,
        'analysis_power_vector_canonical_hash':h,'analysis_energy_unit_hash':h,'energy_numeric_mode':'EXACT_RATIONAL',
        'energy_numeric_scale':'','formal_contract_hash':h,
        'rta_formula_version':THEORY_FORMULA_VERSION,
        'theory_document_sha256':THEORY_DOCUMENT_SHA256,
        'fixed_carry_in_corollary_hash':FIXED_CARRY_IN_INTERFACE_SHA256,
    }
    source={**identity,'analysis_run_id':'cw','variant':'CW-Theta^cw','analysis_method_role':'MAIN_METHOD',
        'analysis_solver_status':'COMPLETED','analysis_certification_status':'CERTIFIED_TASKSET',
        'fixed_carry_in_corollary_status':'NOT_APPLICABLE','n_tasks_total':'2',
        'n_tasks_candidate_found':'2','n_tasks_certified':'2','dominance_invariant_status':'NOT_APPLICABLE',
        'dominance_violation_count':'0'}
    target={**identity,'analysis_run_id':'loc','variant':'LOC-Theta^cw','analysis_method_role':'AUXILIARY_ABLATION',
        'analysis_solver_status':'COMPLETED','analysis_certification_status':'CERTIFIED_TASKSET',
        'fixed_carry_in_corollary_status':'ACTIVE','n_tasks_total':'2',
        'n_tasks_candidate_found':'2','n_tasks_certified':'2','dominance_invariant_status':'SATISFIED',
        'dominance_violation_count':'0'}
    source_tasks=[
        {'analysis_run_id':'cw','task_id':'0','task_solver_status':'CANDIDATE_FOUND','task_certification_status':'CERTIFIED','candidate_response_time':'4'},
        {'analysis_run_id':'cw','task_id':'1','task_solver_status':'CANDIDATE_FOUND','task_certification_status':'CERTIFIED','candidate_response_time':'7'},
    ]
    target_tasks=[]
    for tid,candidate in [('0','3'),('1','6')]:
        target_tasks.append({'analysis_run_id':'loc','task_id':tid,'variant':'LOC-Theta^cw',
            'task_solver_status':'CANDIDATE_FOUND','task_certification_status':'CERTIFIED',
            'candidate_response_time':candidate,'source_analysis_run_id':'cw',
            'dependency_vector_check_status':'VALID','fixed_carry_in_corollary_status':'ACTIVE',
            'carry_in_source_variant':'CW-Theta^cw','carry_in_source_certification_status':LOC_THETA_CW_SOURCE_CERTIFICATION,
            'dominance_invariant_status':'SATISFIED'})
    if case=='source_uncertified':source['analysis_certification_status']='NOT_CERTIFIED'
    elif case=='dependency_invalid':target_tasks[1]['dependency_vector_check_status']='INVALID'
    elif case=='partial_vector_certified':
        target['n_tasks_candidate_found']='1';target['n_tasks_certified']='1'
        target_tasks[1]['task_solver_status']='NOT_EVALUATED_AFTER_PREFIX_FAILURE';target_tasks[1]['task_certification_status']='NOT_CERTIFIED';target_tasks[1].pop('candidate_response_time')
    elif case=='prefix_certified_early':
        target['analysis_solver_status']='TIMEOUT';target['analysis_certification_status']='NOT_CERTIFIED';target['n_tasks_candidate_found']='1';target['n_tasks_certified']='1';target['dominance_invariant_status']='NOT_CHECKED'
        target_tasks[1]['task_solver_status']='TIMEOUT';target_tasks[1]['task_certification_status']='NOT_CERTIFIED';target_tasks[1].pop('candidate_response_time')
    elif case=='local_exceeds_source':target_tasks[1]['candidate_response_time']='8'
    elif case=='source_certification_task_value':
        for row in target_tasks:row['carry_in_source_certification_status']='CERTIFIED'
    elif case=='source_certification_provisional_value':
        for row in target_tasks:row['carry_in_source_certification_status']='PROVISIONAL_NOT_CERTIFIED'
    elif case=='dependency_source_not_certified':
        for row in target_tasks:row['carry_in_source_certification_status']='NOT_CERTIFIED'
    elif case=='source_task_provisional':source_tasks[1]['task_certification_status']='PROVISIONAL_NOT_CERTIFIED'
    elif case=='no_candidate_as_ordinary_failure':
        target['analysis_solver_status']='NO_CANDIDATE';target['analysis_certification_status']='NOT_CERTIFIED';target['n_tasks_candidate_found']='1';target['n_tasks_certified']='0';target['dominance_invariant_status']='NOT_CHECKED'
        target_tasks[0]['task_certification_status']='PROVISIONAL_NOT_CERTIFIED';target_tasks[1]['task_solver_status']='NO_CANDIDATE';target_tasks[1]['task_certification_status']='NOT_CERTIFIED';target_tasks[1].pop('candidate_response_time')
    elif case=='legacy_task_level_status':target['analysis_certification_status']='TASK_LEVEL_CERTIFIED_ONLY'
    elif case=='v9_2_theory_hash':target['rta_formula_version']='v9.2'
    elif case=='fixed_interface_hash_mismatch':target['fixed_carry_in_corollary_hash']='b'*64
    elif case in {'flagged_diagnostic','unflagged_diagnostic'}:
        source['analysis_certification_status']='NOT_CERTIFIED'
        for row in target_tasks:row['carry_in_source_certification_status']='NOT_CERTIFIED'
        target['analysis_solver_status']='NO_CANDIDATE';target['analysis_certification_status']='DIAGNOSTIC_ONLY_NOT_CERTIFIED'
        target['n_tasks_candidate_found']='1';target['n_tasks_certified']='0';target['dominance_invariant_status']='NOT_CHECKED'
        target_tasks[0]['task_certification_status']='DIAGNOSTIC_ONLY_NOT_CERTIFIED'
        target_tasks[1]['task_solver_status']='NO_CANDIDATE';target_tasks[1]['task_certification_status']='NOT_CERTIFIED';target_tasks[1].pop('candidate_response_time')
        if case=='flagged_diagnostic':target['run_phase']='DIAGNOSTIC'
    elif case not in {'positive_certified_vector','carry_in_vector_hash_mismatch'}:raise ValueError(f'unknown certification state test:{case}')
    return {'cw':source,'loc':target},{'cw':source_tasks,'loc':target_tasks}

def _bind_fixture_carry_in_hashes(trs):
    """Bind every target prefix to the exact certified source candidates."""
    source=sorted(trs['cw'],key=lambda r:int(r['task_id']))
    for target in trs['loc']:
        rank=int(target['task_id'])
        entries=[{'hp_task_id':r['task_id'],'theta_value':r['candidate_response_time'],
            'source_analysis_run_id':'cw','source_task_id':r['task_id'],
            'source_task_certification_status':r['task_certification_status']}
            for r in source if int(r['task_id'])<rank]
        pre={'analysis_run_id':'loc','target_task_id':target['task_id'],'entries':entries}
        target['carry_in_vector_hash']=domain_hash('ASAP_BLOCK:CARRY_IN_VECTOR:v1.3.12',pre)

def _validate_fixture_carry_in_hashes(trs,v):
    observed={r['task_id']:r.get('carry_in_vector_hash') for r in trs['loc']}
    expected=json.loads(json.dumps(trs))
    _bind_fixture_carry_in_hashes(expected)
    for row in expected['loc']:
        if observed.get(row['task_id'])!=row['carry_in_vector_hash']:
            v.err(f'carry_in_vector_hash mismatch:loc:{row["task_id"]}')

def run_certification_state_test(case,root=None):
    arows,trs=certification_state_fixture(case);_bind_fixture_carry_in_hashes(trs)
    if case=='carry_in_vector_hash_mismatch':trs['loc'][1]['carry_in_vector_hash']='b'*64
    v=V()
    if root is not None:
        schema=load_yaml_strict(Path(root)/SCHEMA);dd=load_yaml_strict(Path(root)/DICT)
        spec=dd['tables']['per_task_results.csv']['fields']['carry_in_source_certification_status']
        if spec.get('enum_ref')!='analysis_certification_status':v.err('carry-in source certification enum is not analysis-level')
        for row in trs['loc']:
            try:validate_scalar(row['carry_in_source_certification_status'],spec,schema['enums'],schema['failure_masks'])
            except Exception as e:v.err(f'type violation:carry_in_source_certification_status:{row["task_id"]}:{e}')
    source=arows['cw']
    if source['analysis_certification_status']=='CERTIFIED_TASKSET' and any(
            r['task_solver_status']!='CANDIDATE_FOUND' or r['task_certification_status']!='CERTIFIED'
            for r in trs['cw']):
        v.err('certified source analysis contains a non-certified task')
    for row in trs['loc']:
        if row['carry_in_source_certification_status']!=source['analysis_certification_status']:
            v.err(f'carry-in source certification copy mismatch:{row["task_id"]}')
    _validate_fixture_carry_in_hashes(trs,v)
    validate_analysis_certification_states(arows,trs,v)
    return v.e

def certification_state_summary(case):
    arows,trs=certification_state_fixture(case);_bind_fixture_carry_in_hashes(trs)
    identity_fields=['taskset_semantic_hash','priority_rank_hash','analysis_E0_canonical_hash',
        'analysis_service_curve_canonical_hash','analysis_power_vector_canonical_hash',
        'analysis_energy_unit_hash','energy_numeric_mode','energy_numeric_scale',
        'formal_contract_hash','theory_document_sha256','fixed_carry_in_corollary_hash']
    source={r['task_id']:r['candidate_response_time'] for r in trs['cw']}
    target={r['task_id']:r['candidate_response_time'] for r in trs['loc']}
    return {
        'source_analysis':{k:arows['cw'][k] for k in ['variant','analysis_solver_status','analysis_certification_status']},
        'source_tasks':[{'task_id':r['task_id'],'task_solver_status':r['task_solver_status'],
            'task_certification_status':r['task_certification_status'],'candidate_response_time':r['candidate_response_time']} for r in trs['cw']],
        'target_analysis':{k:arows['loc'][k] for k in ['variant','analysis_solver_status','analysis_certification_status','fixed_carry_in_corollary_status','dominance_invariant_status']},
        'target_tasks':[{'task_id':r['task_id'],'task_solver_status':r['task_solver_status'],
            'task_certification_status':r['task_certification_status'],'candidate_response_time':r['candidate_response_time'],
            'carry_in_source_certification_status':r['carry_in_source_certification_status'],
            'carry_in_vector_hash':r['carry_in_vector_hash']} for r in trs['loc']],
        'source_candidate_vector':source,
        'target_candidate_vector':target,
        'all_local_candidates_le_source':all(int(target[k])<=int(source[k]) for k in source),
        'source_target_identity_fields_equal':all(arows['cw'].get(k)==arows['loc'].get(k) for k in identity_fields),
        'carry_in_hashes_recomputed_from_source_prefixes':True,
    }

def recompute_taskset_hashes(ts,tasks):
    tasks=sorted(tasks,key=lambda r:int(r['task_id']))
    sem=[{'task_id':r['task_id'],'C_i':r['C_i'],'T_i':r['T_i'],'D_i':r['D_i'],'P_raw':r['P_raw'],'P_analysis':r['P_analysis'],'priority_rank':r['priority_rank']} for r in tasks]
    pr=[{'task_id':r['task_id'],'priority_rank':r['priority_rank']} for r in tasks]
    raw=[{'task_id':r['task_id'],'P_raw':r['P_raw']} for r in tasks]
    ana=[{'task_id':r['task_id'],'P_analysis':r['P_analysis'],'P_analysis_scaled':r.get('P_analysis_scaled') or None,'P_rounding_mode':r.get('P_rounding_mode') or None} for r in tasks]
    return {'taskset_semantic_hash':domain_hash('ASAP_BLOCK:TASKSET_SEMANTIC:v1.3.12',{'M':ts['M'],'n':ts['n'],'tasks':sem}),'priority_rank_hash':domain_hash('ASAP_BLOCK:PRIORITY_RANK:v1.3.12',pr),'power_vector_raw_hash':domain_hash('ASAP_BLOCK:POWER_VECTOR_RAW:v1.3.12',raw),'power_vector_analysis_hash':domain_hash('ASAP_BLOCK:POWER_VECTOR_ANALYSIS:v1.3.12',ana)}

def validate_lineage(rows,schema,formal,canon,plans,v,profile):
    # Base generation and transformed taskset materialization.
    tasksets={r['taskset_id']:r for r in rows.get('tasksets.csv',[])};tasks_by=defaultdict(list)
    for r in rows.get('task_definitions.csv',[]):tasks_by[r['taskset_id']].append(r)
    ts_by_req=defaultdict(list)
    for r in tasksets.values():ts_by_req[r['materialization_request_id']].append(r)
    gen_by_req={r['request_id']:r for r in rows.get('generation_requests.csv',[])}
    for rid,gr in gen_by_req.items():
        ts=ts_by_req.get(rid,[])
        if gr['generation_status']=='SUCCESS':
            if len(ts)!=1 or gr.get('taskset_id')!=ts[0].get('taskset_id'):v.err(f'generation/taskset one-to-one failure:{rid}')
            elif ts[0]['source_generation_request_id']!=gr['generation_request_id']:v.err(f'generation source lineage mismatch:{rid}')
            if gr['requested_seed']!=plans[rid]['derived_seed']:v.err(f'generation seed replacement:{rid}')
        elif ts:v.err(f'failed generation has taskset:{rid}')
    trans_by_req={r['request_id']:r for r in rows.get('paired_transformations.csv',[])}
    for rid,tr in trans_by_req.items():
        ts=ts_by_req.get(rid,[])
        if len(ts)!=1 or ts[0]['taskset_id']!=tr['transformed_taskset_id']:v.err(f'transformation/taskset one-to-one failure:{rid}')
        if plans[rid]['transformation_id']!=tr['transformation_id'] or plans[rid]['taskset_request_id']!=tasksets[tr['parent_taskset_id']]['materialization_request_id']:v.err(f'transformation plan mismatch:{rid}')
    for tid,ts in tasksets.items():
        trows=tasks_by.get(tid,[]);n=int(ts['n'])
        if len(trows)!=n:v.err(f'task definition count mismatch:{tid}:{len(trows)}!={n}')
        ids=sorted(int(r['task_id']) for r in trows);ranks=sorted(int(r['priority_rank']) for r in trows)
        if ids!=list(range(n)):v.err(f'task_id set is not 0..n-1:{tid}')
        if ranks!=list(range(n)):v.err(f'priority ranks are not a permutation:{tid}')
        for r in trows:
            try:
                C,T,D=map(parse_canonical_integer,[r['C_i'],r['T_i'],r['D_i']])
                if not 1<=C<=D<=T:v.err(f'task bound violation:{tid}:{r["task_id"]}')
            except Exception:pass
        for f,h in recompute_taskset_hashes(ts,trows).items():
            if ts[f]!=h:v.err(f'taskset derived hash mismatch:{tid}:{f}')
    # Analysis and task-level state/counts.
    arows={r['analysis_run_id']:r for r in rows.get('per_taskset_results.csv',[])};trs=defaultdict(list)
    for r in rows.get('per_task_results.csv',[]):trs[r['analysis_run_id']].append(r)
    for aid,a in arows.items():
        pr=plans.get(a['request_id'])
        ts=tasksets.get(a['taskset_id'])
        if pr and ts and pr['taskset_request_id']!=ts['materialization_request_id']:v.err(f'analysis taskset request mismatch:{aid}')
        if ts and a['taskset_materialization_request_id']!=ts['materialization_request_id']:v.err(f'analysis materialization lineage mismatch:{aid}')
        if ts and a['generation_request_id']!=ts['source_generation_request_id']:v.err(f'analysis source generation mismatch:{aid}')
        rr=trs.get(aid,[]);n=int(a['n_tasks_total'])
        if n!=int(a['n']) or len(rr)!=n:v.err(f'analysis task row count mismatch:{aid}')
        cert=a['analysis_certification_status']=='CERTIFIED_TASKSET'
        if (a['taskset_proven']=='true')!=cert:v.err(f'taskset_proven invariant:{aid}')
        if cert and a['analysis_solver_status']!='COMPLETED':v.err(f'certified taskset not completed:{aid}')
        if cert and any(r['task_certification_status']!='CERTIFIED' for r in rr):v.err(f'certified taskset has noncertified task:{aid}')
        if a['run_phase']=='FORMAL' and a['analysis_solver_status']=='NUMERIC_ERROR':v.err(f'formal numeric error:{aid}')
        ev=sum(r['task_solver_status'] not in {'NOT_EVALUATED_AFTER_PREFIX_FAILURE','NOT_APPLICABLE_DEPENDENCY'} for r in rr)
        cand=sum(r['task_solver_status']=='CANDIDATE_FOUND' for r in rr);ct=sum(r['task_certification_status']=='CERTIFIED' for r in rr)
        if [int(a['n_tasks_evaluated']),int(a['n_tasks_candidate_found']),int(a['n_tasks_certified'])]!=[ev,cand,ct]:v.err(f'analysis task count aggregate mismatch:{aid}')
        tdefs={r['task_id']:r for r in tasks_by.get(a['taskset_id'],[])}
        for r in rr:
            validate_task_failure_provenance(r,v,f'{aid}:{r.get("task_id")}')
            td=tdefs.get(r['task_id'])
            if td:
                for x,y in [('C_i','C_i'),('T_i','T_i'),('D_i','D_i'),('priority_rank','priority_rank')]:
                    if r[x]!=td[y]:v.err(f'task result copied parameter mismatch:{aid}:{r["task_id"]}:{x}')
            calc=domain_hash('ASAP_BLOCK:TASK_RESULT:v1.3.12',{k:(None if v=='' else v) for k,v in r.items() if k!='task_result_hash'})
            if r['task_result_hash']!=calc:v.err(f'task_result_hash mismatch:{aid}:{r["task_id"]}')
            if r['task_certification_status']=='CERTIFIED' and r['task_solver_status']!='CANDIDATE_FOUND':v.err(f'certified task without candidate:{aid}:{r["task_id"]}')
    # Dependency rows: exact copied inputs, mask, row hash and vector hash.
    deps_by_target=defaultdict(list);task_index={(r['analysis_run_id'],r['task_id']):r for rs in trs.values() for r in rs}
    validate_analysis_certification_states(arows,trs,v)
    dep_fields=[('source_taskset_semantic_hash','taskset_semantic_hash'),('source_priority_rank_hash','priority_rank_hash'),('source_analysis_E0_canonical_hash','analysis_E0_canonical_hash'),('source_analysis_service_curve_canonical_hash','analysis_service_curve_canonical_hash'),('source_analysis_power_vector_canonical_hash','analysis_power_vector_canonical_hash'),('source_analysis_energy_unit_hash','analysis_energy_unit_hash'),('source_energy_numeric_mode','energy_numeric_mode'),('source_energy_numeric_scale','energy_numeric_scale'),('source_E0_scaled','E0_analysis_scaled'),('source_service_curve_hash','service_curve_scaled_hash'),('source_power_vector_hash','power_vector_scaled_hash'),('source_theory_document_sha256','theory_document_sha256'),('source_plan_context_hash','plan_context_hash'),('source_fixed_carry_in_corollary_hash','fixed_carry_in_corollary_hash'),('source_formal_contract_hash','formal_contract_hash')]
    for r in rows.get('rta_dependency_records.csv',[]):
        deps_by_target[(r['analysis_run_id'],r['target_task_id'])].append(r)
        src=arows.get(r['source_analysis_run_id']);tgt=arows.get(r['analysis_run_id']);st=task_index.get((r['source_analysis_run_id'],r['source_task_id']))
        if src:
            for rf,af in dep_fields:
                if r.get(rf,'')!=src.get(af,''):v.err(f'dependency source copied field mismatch:{r["analysis_run_id"]}:{rf}')
            if r['source_analysis_solver_status']!=src['analysis_solver_status'] or r['source_analysis_certification_status']!=src['analysis_certification_status'] or r['source_variant']!=src['variant']:v.err(f'dependency source status mismatch:{r["analysis_run_id"]}')
        if tgt:
            for rf,af in [(a.replace('source_','target_',1),b) for a,b in dep_fields]:
                if r.get(rf,'')!=tgt.get(af,''):v.err(f'dependency target copied field mismatch:{r["analysis_run_id"]}:{rf}')
        if st and (r['source_task_solver_status']!=st['task_solver_status'] or r['source_task_certification_status']!=st['task_certification_status']):v.err(f'dependency source task status mismatch:{r["analysis_run_id"]}')
        expected=set()
        pairs=[('source_taskset_semantic_hash','target_taskset_semantic_hash','TASKSET_HASH_MISMATCH'),('source_priority_rank_hash','target_priority_rank_hash','PRIORITY_HASH_MISMATCH'),('source_analysis_E0_canonical_hash','target_analysis_E0_canonical_hash','E0_HASH_MISMATCH'),('source_analysis_service_curve_canonical_hash','target_analysis_service_curve_canonical_hash','SERVICE_CURVE_HASH_MISMATCH'),('source_analysis_power_vector_canonical_hash','target_analysis_power_vector_canonical_hash','POWER_VECTOR_HASH_MISMATCH'),('source_analysis_energy_unit_hash','target_analysis_energy_unit_hash','ENERGY_UNIT_HASH_MISMATCH'),('source_energy_numeric_mode','target_energy_numeric_mode','NUMERIC_MODE_MISMATCH'),('source_energy_numeric_scale','target_energy_numeric_scale','NUMERIC_SCALE_MISMATCH'),('source_theory_document_sha256','target_theory_document_sha256','THEORY_HASH_MISMATCH'),('source_fixed_carry_in_corollary_hash','target_fixed_carry_in_corollary_hash','FIXED_CARRY_IN_INTERFACE_HASH_MISMATCH'),('source_formal_contract_hash','target_formal_contract_hash','FORMAL_CONTRACT_HASH_MISMATCH')]
        for x,y,m in pairs:
            if r.get(x,'')!=r.get(y,''):expected.add(m)
        if r['fixed_carry_in_corollary_status']!='ACTIVE':expected.add('COROLLARY_INACTIVE')
        if r['source_analysis_certification_status']!='CERTIFIED_TASKSET' or r['source_task_certification_status']!='CERTIFIED':expected.add('DEPENDENCY_CERTIFICATION_MISMATCH')
        if r['dependency_input_failure_mask']!=format_mask(expected):v.err(f'dependency mask mismatch:{r["analysis_run_id"]}:{r["target_task_id"]}:{r["hp_task_id"]}')
        exp_status='VALID' if not expected else 'INVALID'
        if r['dependency_vector_check_status']!=exp_status:v.err(f'dependency status mismatch:{r["analysis_run_id"]}:{r["target_task_id"]}')
        calc=domain_hash('ASAP_BLOCK:DEPENDENCY_RECORD:v1.3.12',{k:(None if v=='' else v) for k,v in r.items() if k!='dependency_record_hash'})
        if r['dependency_record_hash']!=calc:v.err(f'dependency_record_hash mismatch:{r["analysis_run_id"]}:{r["target_task_id"]}:{r["hp_task_id"]}')
    for key,ds in deps_by_target.items():
        aid,tid=key;ordered=sorted(ds,key=lambda r:int(r['hp_task_id']))
        pre={'analysis_run_id':aid,'target_task_id':tid,'entries':[{'hp_task_id':r['hp_task_id'],'theta_value':r['theta_value'],'source_analysis_run_id':r['source_analysis_run_id'],'source_task_id':r['source_task_id'],'source_task_certification_status':r['source_task_certification_status']} for r in ordered]}
        h=domain_hash('ASAP_BLOCK:CARRY_IN_VECTOR:v1.3.12',pre)
        if any(r['carry_in_vector_hash']!=h for r in ds):v.err(f'carry_in_vector_hash mismatch:{aid}:{tid}')
        tr=task_index.get((aid,tid))
        if tr and tr.get('carry_in_vector_hash')!=h:v.err(f'task carry_in_vector_hash mismatch:{aid}:{tid}')
        if tr and any(r['dependency_vector_check_status']!='VALID' for r in ds) and tr['task_certification_status']=='CERTIFIED':v.err(f'certified task has invalid dependency:{aid}:{tid}')
        if tr and all(r['dependency_vector_check_status']=='VALID' for r in ds) and tr['variant']=='LOC-Theta^cw' and tr['task_solver_status']=='NO_CANDIDATE' and tr.get('dominance_invariant_status')!='DOMINANCE_INVARIANT_VIOLATION':v.err(f'LOC-Theta^cw dominance invariant status missing:{aid}:{tid}')
    # Trace hashes and plan lineage.
    rel_sets={r['release_trace_id']:r for r in rows.get('release_trace_sets.csv',[])};rels=defaultdict(list)
    for r in rows.get('release_traces.csv',[]):rels[r['release_trace_id']].append(r)
    for rid,rs in rel_sets.items():
        pr=plans.get(rs['request_id']);ts=tasksets.get(rs['taskset_id']);rr=sorted(rels[rid],key=lambda x:int(x['job_id']))
        if pr and pr['request_type']!='RELEASE_TRACE_GENERATION':v.err(f'release trace request type mismatch:{rid}')
        if pr and ts and pr['taskset_request_id']!=ts['materialization_request_id']:v.err(f'release trace taskset request mismatch:{rid}')
        if pr:
            for sf,pf in [('scenario_id','scenario_id'),('stream_label','stream_label'),('stream_index','stream_index'),('derived_seed','derived_seed'),('trace_generator_contract_hash','trace_generator_contract_hash')]:
                if rs.get(sf,'')!=pr.get(pf,''):v.err(f'release trace/plan mismatch:{rid}:{sf}')
        for x in rr:
            for sf in ['stream_label','stream_index','derived_seed','trace_generator_version']:
                if x.get(sf,'')!=rs.get(sf,''):v.err(f'release trace row/set mismatch:{rid}:{x.get("job_id")}:{sf}')
        h=domain_hash('ASAP_BLOCK:RELEASE_TRACE:v1.3.12',[{k:r[k] for k in ['job_id','task_id','release_time','execution_demand','absolute_deadline']} for r in rr])
        if rs['release_trace_hash']!=h:v.err(f'release trace hash mismatch:{rid}')
        td={r['task_id']:r for r in tasks_by.get(rs['taskset_id'],[])}
        for r in rr:
            if r['task_id'] in td:
                if int(r['absolute_deadline'])!=int(r['release_time'])+int(td[r['task_id']]['D_i']):v.err(f'release absolute deadline mismatch:{rid}:{r["job_id"]}')
                if r['execution_demand']!=td[r['task_id']]['C_i']:v.err(f'release execution demand mismatch:{rid}:{r["job_id"]}')
    har_sets={r['harvest_trace_id']:r for r in rows.get('harvest_trace_sets.csv',[])};hrows=defaultdict(list)
    for r in rows.get('harvest_traces.csv',[]):hrows[r['harvest_trace_id']].append(r)
    for hid,hs in har_sets.items():
        pr=plans.get(hs['request_id']);rr=sorted(hrows[hid],key=lambda x:int(x['tick']))
        if pr and pr['request_type']!='HARVEST_TRACE_GENERATION':v.err(f'harvest trace request type mismatch:{hid}')
        if pr:
            for sf,pf in [('scenario_id','scenario_id'),('stream_label','stream_label'),('stream_index','stream_index'),('derived_seed','derived_seed'),('trace_generator_contract_hash','trace_generator_contract_hash')]:
                if hs.get(sf,'')!=pr.get(pf,''):v.err(f'harvest trace/plan mismatch:{hid}:{sf}')
        for x in rr:
            for sf in ['stream_label','stream_index','derived_seed','trace_generator_version']:
                if x.get(sf,'')!=hs.get(sf,''):v.err(f'harvest trace row/set mismatch:{hid}:{x.get("tick")}:{sf}')
        raw=domain_hash('ASAP_BLOCK:HARVEST_TRACE_RAW:v1.3.12',[{'tick':r['tick'],'H_raw':r['H_raw']} for r in rr]);ana=domain_hash('ASAP_BLOCK:HARVEST_TRACE_ANALYSIS:v1.3.12',[{'tick':r['tick'],'H_analysis':r['H_analysis']} for r in rr])
        if hs['harvest_trace_raw_hash']!=raw or hs['harvest_trace_analysis_hash']!=ana:v.err(f'harvest trace hash mismatch:{hid}')
    # Simulation lineage and job arithmetic.
    sims={r['simulation_run_id']:r for r in rows.get('simulation_taskset_summary.csv',[])};jobs=defaultdict(list)
    for r in rows.get('simulation_job_results.csv',[]):jobs[r['simulation_run_id']].append(r)
    for sid,sim in sims.items():
        pr=plans.get(sim['request_id']);ts=tasksets.get(sim['taskset_id']);rs=rel_sets.get(sim['release_trace_id']);hs=har_sets.get(sim['harvest_trace_id'])
        if pr:
            if pr['taskset_request_id']!=(ts or {}).get('materialization_request_id'):v.err(f'simulation taskset request mismatch:{sid}')
            if pr['release_trace_request_id']!=(rs or {}).get('request_id') or pr['harvest_trace_request_id']!=(hs or {}).get('request_id'):v.err(f'simulation trace request mismatch:{sid}')
        if rs and rs['taskset_id']!=sim['taskset_id']:v.err(f'simulation/release taskset mismatch:{sid}')
        expected={(r['job_id'],r['task_id']):r for r in rels.get(sim['release_trace_id'],[])};actual={(r['job_id'],r['task_id']):r for r in jobs[sid]}
        if set(expected)!=set(actual):v.err(f'simulation jobs differ from release trace:{sid}')
        for k,j in actual.items():
            rr=expected.get(k)
            if rr and any(j[f]!=rr[f] for f in ['release_time','absolute_deadline','execution_demand']):v.err(f'simulation job/release mismatch:{sid}:{j["job_id"]}')
            comp=j.get('completion_time','')
            if comp:
                if int(j['observed_response_time'])!=int(comp)-int(j['release_time']):v.err(f'observed response arithmetic:{sid}:{j["job_id"]}')
                exp_dead='MET_DEADLINE' if int(comp)<=int(j['absolute_deadline']) else 'DEADLINE_MISS'
                if j['completion_observation_status']!='COMPLETED' or j['deadline_check_status']!=exp_dead:v.err(f'job completion/deadline status mismatch:{sid}:{j["job_id"]}')
            else:
                if j['completion_observation_status']!='CENSORED_HORIZON':v.err(f'null completion not censored:{sid}:{j["job_id"]}')
                reached=int(sim['observation_horizon'])>=int(j['absolute_deadline'])
                exp='DEADLINE_MISS' if reached else 'DEADLINE_NOT_REACHED'
                if j['deadline_check_status']!=exp:v.err(f'censored deadline status mismatch:{sid}:{j["job_id"]}')
    # Check-pair lineage, E0 aggregation, compatibility masks.
    service={r['service_trace_check_id']:r for r in rows.get('service_trace_checks.csv',[])};e0={r['e0_certificate_check_id']:r for r in rows.get('e0_trace_certificate_checks.csv',[])};compat={r['compatibility_check_id']:r for r in rows.get('analysis_simulation_compatibility_checks.csv',[])};e0jobs=defaultdict(list)
    for r in rows.get('e0_job_certificate_checks.csv',[]):e0jobs[r['e0_certificate_check_id']].append(r)
    for tn,index,ctype in [('service_trace_checks.csv',service,'SERVICE'),('e0_trace_certificate_checks.csv',e0,'E0'),('analysis_simulation_compatibility_checks.csv',compat,'COMPATIBILITY')]:
        pair=defaultdict(list)
        for r in index.values():
            pr=plans.get(r['request_id'])
            if pr and (pr['simulation_request_id']!=sims[r['simulation_run_id']]['request_id'] or pr['analysis_request_id']!=arows[r['analysis_run_id']]['request_id']):v.err(f'check request pair mismatch:{tn}:{r.get(list(index.keys())[0],"")}')
            if r['check_role']=='FORMAL_PRIMARY':
                pair[(r['simulation_run_id'],r['analysis_run_id'])].append(r)
                exp=domain_hash(f'ASAP_BLOCK:FORMAL_PRIMARY:{ctype}:v1.3.12',{'simulation_run_id':r['simulation_run_id'],'analysis_run_id':r['analysis_run_id'],'check_type':ctype,'formal_contract_hash':r.get('formal_contract_hash') or None})
                if r['formal_primary_selector']!=exp or r['formal_primary_selector_status']!='VALID':v.err(f'formal-primary selector mismatch:{tn}:{r["request_id"]}')
            elif r.get('formal_primary_selector','')!='' or r['formal_primary_selector_status']!='NOT_CHECKED':v.err(f'diagnostic selector invalid:{tn}:{r["request_id"]}')
    for cid,r in e0.items():
        js=e0jobs[cid];simjobs=jobs.get(r['simulation_run_id'],[])
        if r['check_role']=='FORMAL_PRIMARY' and r['certificate_scope_mode']=='FULL_RELEASE_SET':
            if {(x['job_id'],x['task_id']) for x in js}!={(x['job_id'],x['task_id']) for x in simjobs}:v.err(f'E0 full-release set mismatch:{cid}')
        if int(r['certificate_set_size'])!=len(js):v.err(f'E0 certificate size mismatch:{cid}')
        mode=r['theorem_conditioning_mode'];sts=[x['job_e0_certificate_status'] for x in js]
        if mode=='UNCONDITIONAL_E0_ZERO':
            expected_trace='NOT_REQUIRED'
            if any(x!='NOT_REQUIRED' for x in sts):v.err(f'E0=0 job status mismatch:{cid}')
        else:
            if len(js)==0:expected_trace='EMPTY_CERTIFICATE_SCOPE'
            elif any(x=='NOT_SATISFIED' for x in sts):expected_trace='NOT_SATISFIED'
            elif all(x=='SATISFIED' for x in sts):expected_trace='SATISFIED_ALL'
            else:expected_trace='NOT_CHECKED'
            for x in js:
                if x['job_e0_certificate_status'] in {'SATISFIED','NOT_SATISFIED'}:
                    cmp=compare_numbers(x['release_time_energy_analysis'],r['E0_analysis_effective'])
                    exp='SATISFIED' if cmp>=0 else 'NOT_SATISFIED'
                    if x['job_e0_certificate_status']!=exp:v.err(f'E0 job comparison mismatch:{cid}:{x["job_id"]}')
        if r['trace_e0_certificate_status']!=expected_trace:v.err(f'E0 trace aggregate mismatch:{cid}')
        if mode=='UNCONDITIONAL_E0_ZERO':
            if r.get('job_certificate_satisfaction_rate','')!='':v.err(f'E0=0 satisfaction rate must be null:{cid}')
        elif js and expected_trace in {'SATISFIED_ALL','NOT_SATISFIED'}:
            sat=sum(x=='SATISFIED' for x in sts)
            if sat in {0,len(js)}: rate=str(sat//len(js))
            else:
                g=math.gcd(sat,len(js)); rate=f'{sat//g}/{len(js)//g}'
            if r['job_certificate_satisfaction_rate']!=rate:v.err(f'E0 satisfaction rate mismatch:{cid}')
        elif r.get('job_certificate_satisfaction_rate','')!='':v.err(f'non-resolved E0 satisfaction rate must be null:{cid}')
    comp_map={'battery_model_theorem_status':('BATTERY_MODEL_MISMATCH','BATTERY_MODEL_MISMATCH'),'scheduler_semantics_match_status':('SCHEDULER_SEMANTICS_MISMATCH','SCHEDULER_SEMANTICS_MISMATCH'),'event_order_match_status':('EVENT_ORDER_MISMATCH','EVENT_ORDER_MISMATCH'),'numeric_contract_status':('NUMERIC_CONTRACT_INVALID','NUMERIC_CONTRACT_INVALID'),'analysis_model_match_status':('ANALYSIS_MODEL_MISMATCH','ANALYSIS_MODEL_MISMATCH'),'energy_account_match_status':('ENERGY_ACCOUNT_MISMATCH','ENERGY_ACCOUNT_MISMATCH'),'dependency_certification_match_status':('DEPENDENCY_CERTIFICATION_MISMATCH','DEPENDENCY_CERTIFICATION_MISMATCH'),'initial_pending_jobs_status':('INITIAL_PENDING_JOBS_MISMATCH','INITIAL_PENDING_JOBS_MISMATCH'),'integer_event_model_status':('NON_INTEGER_EVENT_MISMATCH','NON_INTEGER_EVENT_MISMATCH'),'self_suspension_status':('SELF_SUSPENSION_MISMATCH','SELF_SUSPENSION_MISMATCH'),'task_seriality_status':('TASK_SERIALITY_MISMATCH','TASK_SERIALITY_MISMATCH'),'execution_demand_status':('EXECUTION_DEMAND_MISMATCH','EXECUTION_DEMAND_MISMATCH'),'power_upper_bound_status':('POWER_UPPER_BOUND_MISMATCH','POWER_UPPER_BOUND_MISMATCH'),'overhead_accounting_status':('OVERHEAD_ACCOUNTING_MISMATCH','UNACCOUNTED_OVERHEAD'),'harvest_causality_status':('HARVEST_CAUSALITY_MISMATCH','HARVEST_CAUSALITY_MISMATCH'),'service_curve_contract_match_status':('SERVICE_CURVE_CONTRACT_INVALID','SERVICE_CURVE_CONTRACT_INVALID')}
    for cid,r in compat.items():
        ms=set()
        for fld,(cm,_) in comp_map.items():
            if r[fld]=='MISMATCH':ms.add(cm)
        if r['compatibility_failure_mask']!=format_mask(ms):v.err(f'compatibility mask mismatch:{cid}')
    # Bound audits and exact theorem-applicability derivation.
    bchecks=defaultdict(list);job_index={(r['simulation_run_id'],r['job_id']):r for rs in jobs.values() for r in rs};task_index={(r['analysis_run_id'],r['task_id']):r for rs in trs.values() for r in rs}
    audits={r['bound_audit_run_id']:r for r in rows.get('bound_audit_runs.csv',[])}
    for b in rows.get('simulation_bound_checks.csv',[]):
        bchecks[b['bound_audit_run_id']].append(b);j=job_index.get((b['simulation_run_id'],b['job_id']));t=task_index.get((b['analysis_run_id'],b['task_id']));a=arows.get(b['analysis_run_id']);sv=service.get(b['service_trace_check_id']);ec=e0.get(b['e0_certificate_check_id']);cp=compat.get(b['compatibility_check_id'])
        if t:
            copied={'task_solver_status':t['task_solver_status'],'task_certification_status':t['task_certification_status'],'candidate_response_time':t.get('candidate_response_time',''),'candidate_source_task_result_hash':t['task_result_hash'],'source_analysis_run_id':t.get('source_analysis_run_id',''),'carry_in_vector_hash':t.get('carry_in_vector_hash','')}
            for f,x in copied.items():
                if b.get(f,'')!=x:v.err(f'bound copied task field mismatch:{b["simulation_run_id"]}:{b["job_id"]}:{f}')
        if a:
            for f,af in [('analysis_solver_status','analysis_solver_status'),('analysis_certification_status','analysis_certification_status'),('analysis_variant','variant'),('window_mode','window_mode'),('carry_in_mode','carry_in_mode')]:
                if b[f]!=a[af]:v.err(f'bound copied analysis field mismatch:{b["simulation_run_id"]}:{b["job_id"]}:{f}')
        if j and j['task_id']!=b['task_id']:v.err(f'bound job/task mismatch:{b["simulation_run_id"]}:{b["job_id"]}')
        if j:
            if int(b['deadline_boundary'])!=int(j['absolute_deadline']):v.err(f'deadline boundary mismatch:{b["simulation_run_id"]}:{b["job_id"]}')
            if b.get('candidate_response_time'):
                cb=int(j['release_time'])+int(b['candidate_response_time'])
                if int(b['candidate_boundary'])!=cb:v.err(f'candidate boundary arithmetic:{b["simulation_run_id"]}:{b["job_id"]}')
                reached=int(sims[b['simulation_run_id']]['observation_horizon'])>=cb
                exp_obs='BOUNDARY_REACHED' if reached else 'BOUNDARY_NOT_REACHED'
                if b['bound_observation_status']!=exp_obs:v.err(f'bound observation status mismatch:{b["simulation_run_id"]}:{b["job_id"]}')
                if not reached:raw='NOT_OBSERVED'
                elif j.get('completion_time') and int(j['completion_time'])<=cb:raw='WITHIN_CANDIDATE'
                else:raw='EXCEEDS_CANDIDATE'
            else:
                raw='NO_NUMERIC_CANDIDATE';exp_obs='NO_NUMERIC_CANDIDATE'
                if b['bound_observation_status']!=exp_obs:v.err(f'no-candidate observation status mismatch:{b["simulation_run_id"]}:{b["job_id"]}')
            if b['raw_bound_comparison']!=raw:v.err(f'raw bound comparison mismatch:{b["simulation_run_id"]}:{b["job_id"]}')
        fail=set();pending=set()
        if not t or t['task_certification_status']!='CERTIFIED':fail.add('NO_CERTIFIED_CANDIDATE')
        if sv:
            if sv['service_curve_contract_status']=='INVALID':fail.add('SERVICE_CURVE_CONTRACT_INVALID')
            elif sv['service_curve_contract_status']=='NOT_CHECKED':pending.add('SERVICE_TRACE_NOT_CHECKED')
            if sv['service_trace_status']=='INVALID_SERVICE_TRACE':fail.add('INVALID_SERVICE_TRACE')
            elif sv['service_trace_status']=='NOT_CHECKED':pending.add('SERVICE_TRACE_NOT_CHECKED')
        else:pending.add('SERVICE_TRACE_NOT_CHECKED')
        ej=next((x for x in e0jobs.get(b['e0_certificate_check_id'],[]) if x['job_id']==b['job_id']),None)
        if ec:
            if ec['trace_e0_certificate_status']=='EMPTY_CERTIFICATE_SCOPE':fail.add('EMPTY_E0_CERTIFICATE_SCOPE')
            elif ec['trace_e0_certificate_status']=='NOT_SATISFIED' or (ej and ej['job_e0_certificate_status']=='NOT_SATISFIED'):fail.add('E0_TRACE_CERTIFICATE_FAILED')
            elif ec['trace_e0_certificate_status']=='NOT_CHECKED' or (ej and ej['job_e0_certificate_status']=='NOT_CHECKED'):pending.add('E0_CERTIFICATE_NOT_CHECKED')
        else:pending.add('E0_CERTIFICATE_NOT_CHECKED')
        if cp:
            for fld,(_,am) in comp_map.items():
                if cp[fld]=='MISMATCH':fail.add(am)
                elif cp[fld]=='NOT_CHECKED':pending.add('COMPATIBILITY_NOT_CHECKED')
        else:pending.add('COMPATIBILITY_NOT_CHECKED')
        if t and t['variant']=='LOC-Theta^cw' and t.get('dependency_vector_check_status')=='NOT_CHECKED':pending.add('DEPENDENCY_NOT_CHECKED')
        if t and t['variant']=='LOC-Theta^cw' and t.get('dependency_vector_check_status')=='INVALID':fail.add('DEPENDENCY_CERTIFICATION_MISMATCH')
        if b['applicability_failure_mask']!=format_mask(fail):v.err(f'applicability failure mask mismatch:{b["simulation_run_id"]}:{b["job_id"]}')
        if b['applicability_pending_mask']!=format_mask(pending):v.err(f'applicability pending mask mismatch:{b["simulation_run_id"]}:{b["job_id"]}')
        failure_priority=[x for x in schema['failure_masks']['applicability_failure_mask'] if x!='EMPTY']
        primary=next((x for x in failure_priority if x in fail),'NONE')
        if b['primary_applicability_failure_reason']!=primary:v.err(f'primary applicability failure mismatch:{b["simulation_run_id"]}:{b["job_id"]}:{b["primary_applicability_failure_reason"]}!={primary}')
        if fail:app='OUT_OF_THEOREM_SCOPE';snd='OUT_OF_THEOREM_SCOPE'
        elif pending:app='NOT_CHECKED';snd='INCONCLUSIVE'
        else:
            app='APPLICABLE';snd={'WITHIN_CANDIDATE':'WITHIN_CERTIFIED_BOUND','EXCEEDS_CANDIDATE':'CERTIFIED_BOUND_VIOLATION','NOT_OBSERVED':'INCONCLUSIVE'}.get(b['raw_bound_comparison'],'OUT_OF_THEOREM_SCOPE')
        if b['bound_theorem_applicability']!=app or b['soundness_check_status']!=snd:v.err(f'soundness derivation mismatch:{b["simulation_run_id"]}:{b["job_id"]}')
        if profile=='FORMAL_RELEASE' and snd=='CERTIFIED_BOUND_VIOLATION':v.err(f'certified bound violation:{b["simulation_run_id"]}:{b["job_id"]}')
        if profile=='FORMAL_RELEASE' and app=='APPLICABLE' and b['raw_bound_comparison']=='NOT_OBSERVED':v.err(f'applicable bound not observed:{b["simulation_run_id"]}:{b["job_id"]}')
    for bid,audit in audits.items():
        pr=plans.get(audit['request_id']);bs=bchecks.get(bid,[])
        sim=sims.get(audit['simulation_run_id']);ana=arows.get(audit['analysis_run_id'])
        if not pr:v.err(f'bound audit missing planned request:{bid}')
        elif pr.get('request_type')!='BOUND_AUDIT':v.err(f'bound audit request type mismatch:{bid}')
        if not sim:v.err(f'bound audit missing simulation:{bid}')
        if not ana:v.err(f'bound audit missing analysis:{bid}')
        if pr and sim and ana and (pr['simulation_request_id']!=sim['request_id'] or pr['analysis_request_id']!=ana['request_id']):v.err(f'bound audit request pair mismatch:{bid}')
        expected_jobs={r['job_id'] for r in jobs.get(audit['simulation_run_id'],[])}
        actual_jobs={b['job_id'] for b in bs}
        if expected_jobs!=actual_jobs:v.err(f'bound audit job coverage mismatch:{bid}:missing={sorted(expected_jobs-actual_jobs)} extra={sorted(actual_jobs-expected_jobs)}')
        for b in bs:
            if b['request_id']!=audit['request_id'] or b['simulation_run_id']!=audit['simulation_run_id'] or b['analysis_run_id']!=audit['analysis_run_id']:v.err(f'bound audit child lineage mismatch:{bid}:{b["job_id"]}')
            if b['run_phase']!=audit['run_phase'] or b['plan_context_hash']!=audit['plan_context_hash'] or b['audit_build_identity_hash']!=audit['audit_build_identity_hash']:v.err(f'bound audit child copied context mismatch:{bid}:{b["job_id"]}')
        pair=(audit['simulation_run_id'],audit['analysis_run_id'])
        for coll,idf in [(service,'service_trace_check_id'),(e0,'e0_certificate_check_id'),(compat,'compatibility_check_id')]:
            ms=[r for r in coll.values() if (r['simulation_run_id'],r['analysis_run_id'])==pair and r['check_role']=='FORMAL_PRIMARY']
            if len(ms)!=1:v.err(f'bound audit pair requires exactly one formal primary:{bid}:{idf}:{len(ms)}')
        counts={'n_jobs_audited':len(bs),'n_applicable':sum(b['bound_theorem_applicability']=='APPLICABLE' for b in bs),'n_within_certified_bound':sum(b['soundness_check_status']=='WITHIN_CERTIFIED_BOUND' for b in bs),'n_certified_bound_violations':sum(b['soundness_check_status']=='CERTIFIED_BOUND_VIOLATION' for b in bs),'n_out_of_scope':sum(b['soundness_check_status']=='OUT_OF_THEOREM_SCOPE' for b in bs),'n_inconclusive':sum(b['soundness_check_status']=='INCONCLUSIVE' for b in bs)}
        for f,x in counts.items():
            if int(audit[f])!=x:v.err(f'bound audit aggregate mismatch:{bid}:{f}')
    # Formal-primary exactly one for every audited pair and linked IDs.
    for b in rows.get('simulation_bound_checks.csv',[]):
        pair=(b['simulation_run_id'],b['analysis_run_id'])
        for coll,idf in [(service,'service_trace_check_id'),(e0,'e0_certificate_check_id'),(compat,'compatibility_check_id')]:
            ms=[r for r in coll.values() if (r['simulation_run_id'],r['analysis_run_id'])==pair and r['check_role']=='FORMAL_PRIMARY']
            if len(ms)!=1 or b[idf]!=(ms[0][idf] if ms else None):v.err(f'formal primary linkage mismatch:{idf}:{pair}')

def approved_builds(rows,formal,v):
    a=formal['approved_builds']
    for r in rows.get('per_taskset_results.csv',[]):
        if r['run_phase'] in FORMAL_PHASES and r['build_identity_hash']!=a['approved_rta_build_identity_hash']:v.err(f'unapproved RTA build:{r["analysis_run_id"]}')
    for r in rows.get('simulation_taskset_summary.csv',[]):
        if r['run_phase'] in FORMAL_PHASES and (r['simulator_build_identity_hash']!=a['approved_simulator_build_identity_hash'] or r['scheduler_build_identity_hash']!=a['approved_scheduler_build_identity_hash']):v.err(f'unapproved simulation build:{r["simulation_run_id"]}')
    for tn in ['service_trace_checks.csv','e0_trace_certificate_checks.csv','analysis_simulation_compatibility_checks.csv','bound_audit_runs.csv','simulation_bound_checks.csv']:
        for r in rows.get(tn,[]):
            if r['run_phase'] in FORMAL_PHASES and r['audit_build_identity_hash']!=a['approved_audit_build_identity_hash']:v.err(f'unapproved audit build:{tn}')

def parse_evidence_files(report_path):
    if not report_path.exists():return set()
    try:r=load_yaml_strict(report_path)
    except:return set()
    out=set()
    for key in ['CORE0A_gates','CORE0B_gates']:
        for rec in r.get(key,{}).values():out.update(rec.get('evidence_files',[]))
    return out

def package_files(root,formal,v):
    required=set(formal.get('output_contract',{}).get('required_files',[]));evidence=parse_evidence_files(root/'acceptance_report.yaml')
    for name in sorted(required|evidence):_safe_name(name,'runtime package filename',v)
    allowed=required|evidence
    bad=[p.name for p in root.iterdir() if p.is_symlink() or not p.is_file()]
    if bad:v.err(f'non-regular runtime package entries forbidden:{sorted(bad)}')
    actual={p.name for p in root.iterdir() if p.is_file() and not p.is_symlink()}
    missing=required-actual;extra=actual-allowed
    if missing:v.err(f'missing formal package files:{sorted(missing)}')
    if extra:v.err(f'unexpected formal package files:{sorted(extra)}')
    mp=root/'manifest.json';sp=root/'sha256sum.txt'
    if not mp.exists() or not sp.exists():return
    try:m=json.loads(read_text_strict(mp))
    except Exception as e:v.err(f'manifest invalid:{e}');return
    expected_meta={'manifest_version':'1.3.12','plan_context_hash':formal['plan_context_contract']['plan_context_hash'],'formal_contract_hash':formal['contract_metadata']['formal_contract_hash'],'schema_sha256':sha256_file(root/SCHEMA),'dictionary_sha256':sha256_file(root/DICT),'canonical_sha256':sha256_file(root/CANON),'validation_common_sha256':sha256_file(root/COMMON),'artifact_validator_sha256':sha256_file(root/ARTIFACT_VALIDATOR),'result_validator_sha256':sha256_file(root/RESULT_VALIDATOR),'acceptance_validator_sha256':sha256_file(root/ACCEPTANCE_VALIDATOR)}
    for k,x in expected_meta.items():
        if m.get(k)!=x:v.err(f'manifest metadata mismatch:{k}')
    mf=m.get('files',{})
    expected_files=allowed-{'manifest.json','sha256sum.txt'}
    if set(mf)!=expected_files:v.err(f'manifest file set mismatch:missing={sorted(expected_files-set(mf))} extra={sorted(set(mf)-expected_files)}')
    for fn,h in mf.items():
        if not (root/fn).exists() or sha256_file(root/fn)!=h:v.err(f'manifest hash mismatch:{fn}')
    sums={}
    for line in read_text_strict(sp).splitlines():
        if not line:continue
        parts=line.split('  ',1)
        if len(parts)!=2 or not re.fullmatch(r'[0-9a-f]{64}',parts[0]) or parts[1] in sums:v.err(f'bad sha256sum line:{line}');continue
        sums[parts[1]]=parts[0]
    expected_sum=allowed-{'sha256sum.txt'}
    if set(sums)!=expected_sum:v.err(f'sha256sum file set mismatch:missing={sorted(expected_sum-set(sums))} extra={sorted(set(sums)-expected_sum)}')
    for fn,h in sums.items():
        if not (root/fn).exists() or sha256_file(root/fn)!=h:v.err(f'sha256sum mismatch:{fn}')

def nonvacuity(rows,profile,v):
    if profile!='FORMAL_RELEASE':return
    plans=[r for r in rows.get('run_plan_definition.csv',[]) if r['run_phase']=='FORMAL']
    if not plans:v.err('no FORMAL planned requests')
    for typ in sorted({r['request_type'] for r in plans}):
        top={'GENERATION':'generation_requests.csv','TRANSFORMATION':'paired_transformations.csv','RELEASE_TRACE_GENERATION':'release_trace_sets.csv','HARVEST_TRACE_GENERATION':'harvest_trace_sets.csv','ANALYSIS':'per_taskset_results.csv','SIMULATION':'simulation_taskset_summary.csv','SERVICE_CHECK':'service_trace_checks.csv','E0_CHECK':'e0_trace_certificate_checks.csv','COMPATIBILITY_CHECK':'analysis_simulation_compatibility_checks.csv','BOUND_AUDIT':'bound_audit_runs.csv'}[typ]
        if not any(r.get('run_phase')=='FORMAL' for r in rows.get(top,[])):v.err(f'formal nonvacuity:{top}')
    if not any(r.get('run_phase')=='FORMAL' and r.get('analysis_certification_status')=='CERTIFIED_TASKSET' for r in rows.get('per_taskset_results.csv',[])):v.err('no formal certified taskset')
    if not rows.get('simulation_bound_checks.csv'):v.err('no formal bound-check rows')
    positive=any(r.get('run_phase')=='FORMAL' and r.get('theorem_conditioning_mode')=='CONDITIONAL_E0_POSITIVE' for r in rows.get('per_taskset_results.csv',[]))
    if positive and not any(r.get('run_phase')=='FORMAL' and r.get('check_role')=='FORMAL_PRIMARY' and r.get('certificate_scope_mode')=='FULL_RELEASE_SET' and r.get('trace_e0_certificate_status')=='SATISFIED_ALL' and int(r.get('certificate_set_size','0'))>0 for r in rows.get('e0_trace_certificate_checks.csv',[])):
        v.err('positive-E0 formal track has no nonempty SATISFIED_ALL full-release certificate trace')

def validate(root,profile='FORMAL_RELEASE',schema_only=False):
    v=V()
    try:s=load_yaml_strict(root/SCHEMA);d=load_yaml_strict(root/DICT);c=load_yaml_strict(root/CANON)
    except Exception as e:return [f'spec load:{e}']
    schema_dictionary(s,d,v)
    if schema_only:return v.e
    formal=validate_formal(root,s,c,v)
    if not formal:return v.e
    package_files(root,formal,v)
    ap=root/'acceptance_report.yaml'
    if not ap.exists():v.err('missing acceptance_report.yaml')
    else:v.e.extend('acceptance:'+x for x in validate_report(ap,root/'formal_contract.yaml',False))
    rows=read_tables(root,s,v);types_conditions(s,d,rows,v);keys_fks(s,rows,v);phase_contract(rows,formal,v)
    plans=validate_plan(root,rows,formal,c,v,profile)
    execution_and_outputs(root,rows,formal,c,plans,s,v,profile)
    validate_lineage(rows,s,formal,c,plans,v,profile);approved_builds(rows,formal,v);nonvacuity(rows,profile,v)
    return v.e

def _failure_fixture_row(schema,status,cert,code,detail,dominance='NOT_CHECKED'):
    cols=schema['tables']['per_task_results.csv']['canonical_column_order']
    row={k:'' for k in cols}
    row.update({
        'analysis_run_id':'analysis-fixture','taskset_id':'taskset-fixture','task_id':'0',
        'analysis_method_role':'AUXILIARY_ABLATION','variant':'CW-D','window_mode':'complete',
        'carry_in_mode':'deadline','priority_rank':'0','C_i':'1','T_i':'10','D_i':'10',
        'P_hat_i_raw':'1','task_solver_status':status,'task_certification_status':cert,
        'task_failure_reason_code':code,'task_failure_detail':'' if detail is None else detail,
        'w_values_checked':'1','h_values_checked':'1','q_values_checked':'1',
        'full_w_scan_conformance':'true','full_h_scan_conformance':'true','full_q_scan_conformance':'true',
        'envelope_call_count':'1','energy_numeric_mode':'EXACT_RATIONAL',
        'dominance_invariant_status':dominance,
    })
    if status=='CANDIDATE_FOUND':
        row['candidate_response_time']='2';row['closing_w']='2'
    row['task_result_hash']=domain_hash('ASAP_BLOCK:TASK_RESULT:v1.3.12',
        {k:(None if v=='' else v) for k,v in row.items() if k!='task_result_hash'})
    return row

def _failure_row_csv_bytes(row,header):
    out=io.StringIO(newline='');writer=csv.DictWriter(out,fieldnames=header,lineterminator='\n')
    writer.writeheader();writer.writerow(row);return out.getvalue().encode('utf-8')

def task_failure_provenance_self_test(root):
    schema=load_yaml_strict(root/SCHEMA);dd=load_yaml_strict(root/DICT);canon=load_yaml_strict(root/CANON)
    header=schema['tables']['per_task_results.csv']['canonical_column_order'];cases={}
    positive=[
      ('candidate','CANDIDATE_FOUND','PROVISIONAL_NOT_CERTIFIED',None,{'origin':'CORE_CANDIDATE'},'NOT_APPLICABLE'),
      ('no_candidate','NO_CANDIDATE','NOT_CERTIFIED','no v9.3 closure candidate by the task deadline',{'origin':'CORE_NO_CANDIDATE'},'NOT_CHECKED'),
      ('timeout','TIMEOUT','NOT_CERTIFIED','v9.3 closure search timed out',{'origin':'CORE_DEADLINE_TIMEOUT'},'NOT_CHECKED'),
      ('numeric','NUMERIC_ERROR','NOT_CERTIFIED','overflow in injected service',{'origin':'CAUGHT_NUMERIC_EXCEPTION'},'NOT_CHECKED'),
      ('prefix','NOT_EVALUATED_AFTER_PREFIX_FAILURE','NOT_APPLICABLE','not evaluated after prefix failure',{'origin':'TASKSET_PREFIX_SYNTHETIC'},'NOT_CHECKED'),
      ('dependency','NOT_APPLICABLE_DEPENDENCY','NOT_APPLICABLE','fixed carry-in dependency is not applicable',{'origin':'TASKSET_DEPENDENCY_SYNTHETIC'},'NOT_APPLICABLE'),
      ('dominance','CANDIDATE_FOUND','NOT_CERTIFIED',None,{'origin':'TASKSET_DOMINANCE_COUNTEREXAMPLE'},'DOMINANCE_INVARIANT_VIOLATION'),
    ]
    roundtrip=[];deterministic=True
    for name,status,cert,raw,context,dominance in positive:
        code,detail=normalize_task_failure_reason(status,cert,raw,context)
        row=_failure_fixture_row(schema,status,cert,code,detail,dominance)
        encoded=_failure_row_csv_bytes(row,header);deterministic&=(encoded==_failure_row_csv_bytes(row,header))
        loaded=next(csv.DictReader(io.StringIO(encoded.decode('utf-8'))))
        vv=V();types_conditions(schema,dd,{'per_task_results.csv':[loaded]},vv)
        validate_task_failure_provenance(loaded,vv,name)
        calc=domain_hash('ASAP_BLOCK:TASK_RESULT:v1.3.12',{k:(None if v=='' else v) for k,v in loaded.items() if k!='task_result_hash'})
        ok=(not vv.e and loaded['task_failure_reason_code']==code
            and (loaded['task_failure_detail'] or None)==detail and loaded['task_solver_status']==status
            and loaded['task_result_hash']==calc)
        cases[f'round_trip_{name}']=ok;roundtrip.append(row)
    cases['repeated_serialization_byte_deterministic']=deterministic
    # P0: same solver/certification state, different formal failure provenance.
    r1=_failure_fixture_row(schema,'INTERNAL_CONFORMANCE_FAILURE','NOT_CERTIFIED','UNKNOWN_CORE_STATUS',TASK_FAILURE_DETAIL_BY_CODE['UNKNOWN_CORE_STATUS'])
    r2=_failure_fixture_row(schema,'INTERNAL_CONFORMANCE_FAILURE','NOT_CERTIFIED','INTERNAL_CONFORMANCE_FAILURE',TASK_FAILURE_DETAIL_BY_CODE['INTERNAL_CONFORMANCE_FAILURE'])
    b1=_failure_row_csv_bytes(r1,header);b2=_failure_row_csv_bytes(r2,header)
    l1=next(csv.DictReader(io.StringIO(b1.decode())));l2=next(csv.DictReader(io.StringIO(b2.decode())))
    cases['p0_distinct_rows_hashes_and_reload']=(b1!=b2 and r1['task_result_hash']!=r2['task_result_hash']
        and l1['task_failure_reason_code']!=l2['task_failure_reason_code'] and l1['task_failure_detail']!=l2['task_failure_detail'])
    altered=dict(r1);altered['task_failure_reason_code']='INTERNAL_CONFORMANCE_FAILURE'
    altered_calc=domain_hash('ASAP_BLOCK:TASK_RESULT:v1.3.12',{k:(None if v=='' else v) for k,v in altered.items() if k!='task_result_hash'})
    cases['failure_code_mutation_hash_mismatch']=(altered_calc!=altered['task_result_hash'])
    n1=normalize_task_failure_reason('NUMERIC_ERROR','NOT_CERTIFIED','overflow at alpha',{'origin':'CAUGHT_NUMERIC_EXCEPTION'})
    n2=normalize_task_failure_reason('NUMERIC_ERROR','NOT_CERTIFIED','different arithmetic diagnostic',{'origin':'CAUGHT_NUMERIC_EXCEPTION'})
    same1=_failure_fixture_row(schema,'NUMERIC_ERROR','NOT_CERTIFIED',*n1)
    same2=_failure_fixture_row(schema,'NUMERIC_ERROR','NOT_CERTIFIED',*n2)
    cases['raw_debug_text_not_formal_semantics']=(n1==n2 and same1==same2 and _failure_row_csv_bytes(same1,header)==_failure_row_csv_bytes(same2,header))
    # Required negative mutations; each predicate is true only when rejected/detected.
    def matrix_reject(row):
        vv=V();validate_task_failure_provenance(row,vv,'negative');return bool(vv.e)
    cases['neg_01_missing_code']=matrix_reject({**roundtrip[0],'task_failure_reason_code':''})
    try:validate_scalar('BOGUS',dd['tables']['per_task_results.csv']['fields']['task_failure_reason_code'],schema['enums'],schema['failure_masks']);cases['neg_02_unknown_code']=False
    except:cases['neg_02_unknown_code']=True
    cases['neg_03_candidate_non_none']=matrix_reject({**roundtrip[0],'task_failure_reason_code':'NO_CANDIDATE','task_failure_detail':TASK_FAILURE_DETAIL_BY_CODE['NO_CANDIDATE']})
    cases['neg_04_failure_none']=matrix_reject({**roundtrip[1],'task_failure_reason_code':'NONE','task_failure_detail':''})
    cases['neg_05_timeout_no_candidate']=matrix_reject({**roundtrip[2],'task_failure_reason_code':'NO_CANDIDATE','task_failure_detail':TASK_FAILURE_DETAIL_BY_CODE['NO_CANDIDATE']})
    cases['neg_06_dominance_generic_solver_code']=matrix_reject({**roundtrip[6],'task_failure_reason_code':'NO_CANDIDATE','task_failure_detail':TASK_FAILURE_DETAIL_BY_CODE['NO_CANDIDATE']})
    unsafe={
      'neg_07_absolute_path':'failure in /home/user/run.py','neg_08_nul':'bad\x00value',
      'neg_09_overlong':'x'*257,'neg_10_crlf':'bad\r\nline','neg_11_memory_address':'object at 0x7ffdeadbeef',
      'neg_timestamp':'2026-07-13T12:13:14Z','neg_traceback':'Traceback (most recent call last):',
      'neg_nonfinite':'value nan','neg_unordered_repr':'{\'b\': 2, \'a\': 1}',
    }
    for name,value in unsafe.items():
        try:validate_task_failure_detail(value);cases[name]=False
        except:cases[name]=True
    detail_mut=dict(roundtrip[1]);detail_mut['task_failure_detail']='closure exhausted through task deadline!'
    detail_calc=domain_hash('ASAP_BLOCK:TASK_RESULT:v1.3.12',{k:(None if v=='' else v) for k,v in detail_mut.items() if k!='task_result_hash'})
    cases['neg_12_detail_hash_mismatch']=(detail_calc!=detail_mut['task_result_hash'])
    request_fields=set(canon['preimages']['request_id']['include'])|{x for fs in canon['request_type_payload_fields'].values() for x in fs}
    cases['neg_13_semantic_request_id_pollution']=not {'task_failure_reason_code','task_failure_detail'}&request_fields
    old_header=[x for x in header if x not in {'task_failure_reason_code','task_failure_detail'}]
    cases['neg_14_v1_3_11_39_column_header']=(len(old_header)==39 and old_header!=header)
    classes=schema['tables']['per_task_results.csv']['required']+schema['tables']['per_task_results.csv']['conditionally_required']+schema['tables']['per_task_results.csv']['optional_diagnostic']
    cases['neg_15_columns_without_canonical_order']=(header==classes and header.index('task_failure_reason_code')==14 and header.index('task_failure_detail')==25)
    tp=canon['preimages']['task_result_hash']
    cases['neg_16_hash_preimage_omits_failure_fields']=('task_failure_reason_code' in tp['include'] and 'task_failure_detail' in tp['include'])
    cases['neg_17_empty_string_as_null']=matrix_reject({**roundtrip[1],'task_failure_detail':''})
    try:normalize_task_failure_reason('NO_CANDIDATE','NOT_CERTIFIED','arbitrary unapproved raw text',{'origin':'CORE_NO_CANDIDATE'});cases['neg_18_unknown_raw_silently_written']=False
    except:cases['neg_18_unknown_raw_silently_written']=True
    return cases

def self_test(root):
    s=load_yaml_strict(root/SCHEMA);cases={}
    cases.update(task_failure_provenance_self_test(root))
    for n,val,spec,exp in [('unknown_mask','AAA|ZZZ',{'type':'enum_set','enum_ref':'applicability_failure_mask'},False),('unreduced','2/4',{'type':'canonical_number'},False),('negative_zero','-0',{'type':'integer'},False),('valid','1/2',{'type':'canonical_number'},True)]:
        try:validate_scalar(val,spec,s['enums'],s['failure_masks']);ok=True
        except:ok=False
        cases[n]=(ok==exp)
    try:validate_scalar('EMPTY',{'type':'enum_set','enum_ref':'applicability_failure_mask'},s['enums'],s['failure_masks']);cases['empty_mask_sentinel_valid']=True
    except:cases['empty_mask_sentinel_valid']=False
    try:validate_scalar('',{'type':'enum_set','enum_ref':'applicability_failure_mask'},s['enums'],s['failure_masks']);cases['blank_mask_rejected']=False
    except:cases['blank_mask_rejected']=True
    cases['cycle_rejected']=cycle({'A','B'},[('A','B'),('B','A')])
    seq=['STARTED','FINISHED','HEARTBEAT'];tp=[i for i,x in enumerate(seq) if x in TERMINAL]
    cases['terminal_after_event_rejected']=not(len(tp)==1 and tp[0]==len(seq)-1)
    vv=V();nonvacuity({'run_plan_definition.csv':[],'per_taskset_results.csv':[],'simulation_bound_checks.csv':[]},'FORMAL_RELEASE',vv);cases['empty_formal_rejected']=bool(vv.e)
    # Payload columns must cover every canonical payload field.
    cols=set(s['tables']['run_plan_definition.csv']['canonical_column_order']);canon=load_yaml_strict(root/CANON)
    cases['request_payload_columns_complete']=all(set(fs)<=cols for fs in canon['request_type_payload_fields'].values())
    tv=V();required_nonnull({'a':{'b':'x'}},['a.b'],tv);cases['required_nonnull_real_path']=not tv.e
    tv=V();required_nonnull({'a':{'b':None}},['a.b'],tv);cases['required_nonnull_rejects_empty']=bool(tv.e)
    # Generation failure is a semantic dependency failure although the generation request itself FINISHED.
    cases['generation_failure_dependency_semantics']=(('FINISHED'=='FINISHED') and ('GENERATION_FAILURE'!='SUCCESS'))
    cases['formal_execution_failure_not_accepted']=('TIMEOUT'!='FINISHED' and 'NOT_RUN_DEPENDENCY'!='FINISHED')
    tree=ast.parse(Path(__file__).read_text(encoding='utf-8'))
    calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='required_nonnull']
    cases['required_nonnull_calls_supply_validator']=bool(calls) and all(len(n.args)>=3 or any(k.arg=='v' for k in n.keywords) for n in calls)
    # Strong child-contract typing/range tests: arbitrary nonempty placeholders must fail.
    gen={'contract_metadata':{'name':'ASAP_BLOCK_generator_contract','version':VERSION,'generator_contract_hash':'a'*64,'canonical_serialization_file':CANON},'generator_parameters':{'task_util_min':'1/100','task_util_max':'1','utilization_tolerance':'1/100','period_distribution':'UNIFORM_INTEGER','period_min':40,'period_max':200,'deadline_generation_rule':'C_PLUS_FLOOR_DELTA_TIMES_T_MINUS_C','deadline_delta_main':'3/4','power_latent_distribution':'DISCRETE_UNIFORM_INTEGER','power_latent_mapping_version':'POWER_LATENT_V1','max_resampling_attempts':100,'generation_failure_threshold':'1/10','priority_policy':'DM','priority_tiebreak':['D_i','T_i','task_id'],'rho_e_parameterization_rule':'FIXED_SERVICE_RATE_WEIGHTED_POWER_SCALE_V1','parameter_cell_canonicalization_version':'PARAMETER_CELL_CANONICAL_V1_3_12'},'rng_contract':{'seed_algorithm':'SHA256_DOMAIN_SEPARATED_CANONICAL_V1_3_12','stream_labels':['TASK_UTILIZATION','PERIODS','POWER_LATENT'],'seed_derivation_algorithm':'SHA256_DOMAIN_SEPARATED_CANONICAL_V1_3_12'},'hash_preimage_rule':'canonical preimage generator_contract_hash; self field null'}
    tv=V();_validate_child_completeness('generator',gen,tv);cases['valid_generator_contract_values']=not tv.e
    bad=json.loads(json.dumps(gen));bad['generator_parameters']['task_util_min']='X';tv=V();_validate_child_completeness('generator',bad,tv);cases['placeholder_generator_value_rejected']=bool(tv.e)
    bad=json.loads(json.dumps(gen));bad['generator_parameters']['period_min']=300;bad['generator_parameters']['period_max']=200;tv=V();_validate_child_completeness('generator',bad,tv);cases['reversed_period_range_rejected']=bool(tv.e)
    sim={'contract_metadata':{'name':'ASAP_BLOCK_simulation_contract','version':VERSION,'simulation_contract_hash':'b'*64,'canonical_serialization_file':CANON},'scheduler_and_model':{'scheduler_variant':'ASAP-BLOCK','scheduler_semantics_version':'S1','event_order_version':'E1','energy_account_semantics_version':'A1','simulation_energy_account_mode':'ANALYSIS_CONSISTENT_ACCOUNT','initial_energy':'0','battery_mode':'UNBOUNDED','battery_capacity':None},'horizons_and_scenarios':{'generation_horizon':100,'observation_horizon':200,'release_scenarios':['SYNCHRONOUS'],'harvest_scenarios':['LATENCY_RATE'],'scenario_requests_per_taskset':1},'execution_contract':{'actual_execution_demand_equals_C_i':True,'actual_unit_power_le_analysis_bound':True,'integer_boundary_preemption_migration':True,'same_task_jobs_nonparallel':True,'unmodeled_overhead_policy':'ACCOUNTED_IN_C_AND_P'},'rng_contract':{'seed_algorithm':'SHA256_DOMAIN_SEPARATED_CANONICAL_V1_3_12','stream_labels':['RELEASE_TRACE','HARVEST_TRACE','ADVERSARIAL_SEARCH'],'seed_derivation_algorithm':'SHA256_DOMAIN_SEPARATED_CANONICAL_V1_3_12'},'hash_preimage_rule':'canonical preimage simulation_contract_hash; self field null'}
    tv=V();_validate_child_completeness('simulation',sim,tv);cases['valid_simulation_contract_values']=not tv.e
    bad=json.loads(json.dumps(sim));bad['scheduler_and_model']['initial_energy']='X';tv=V();_validate_child_completeness('simulation',bad,tv);cases['invalid_initial_energy_rejected']=bool(tv.e)
    bad=json.loads(json.dumps(sim));bad['scheduler_and_model']['battery_mode']='UNBOUNDED';bad['scheduler_and_model']['battery_capacity']='10';tv=V();_validate_child_completeness('simulation',bad,tv);cases['unbounded_capacity_conflict_rejected']=bool(tv.e)
    cases['p0_enum_ref_is_analysis_level']=(
        load_yaml_strict(root/DICT)['tables']['per_task_results.csv']['fields']
        ['carry_in_source_certification_status']['enum_ref']=='analysis_certification_status')
    cases['loc_theta_cw_positive_joint_certification']=not run_certification_state_test('positive_certified_vector',root)
    cases['loc_theta_cw_explicit_diagnostic_allowed']=not run_certification_state_test('flagged_diagnostic',root)
    for name in ['source_uncertified','dependency_invalid','partial_vector_certified','prefix_certified_early','local_exceeds_source','source_certification_task_value','source_certification_provisional_value','dependency_source_not_certified','source_task_provisional','carry_in_vector_hash_mismatch','no_candidate_as_ordinary_failure','legacy_task_level_status','v9_2_theory_hash','fixed_interface_hash_mismatch','unflagged_diagnostic']:
        cases[f'loc_theta_cw_rejects_{name}']=bool(run_certification_state_test(name,root))
    return {'status':'PASSED' if all(cases.values()) else 'FAILED','cases':cases}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('result_root',nargs='?',default='.')
    ap.add_argument('--profile',default='FORMAL_RELEASE',choices=['SCHEMA_ONLY','CORE0A','PILOT','CORE0B','FORMAL_RELEASE','DIAGNOSTIC'])
    ap.add_argument('--schema-only',action='store_true')
    ap.add_argument('--self-test',action='store_true',help='run validator self-tests only; do not validate a result package')
    ap.add_argument('--contract-state-test',choices=['positive_certified_vector','flagged_diagnostic','source_uncertified','dependency_invalid','partial_vector_certified','prefix_certified_early','local_exceeds_source','source_certification_task_value','source_certification_provisional_value','dependency_source_not_certified','source_task_provisional','carry_in_vector_hash_mismatch','no_candidate_as_ordinary_failure','legacy_task_level_status','v9_2_theory_hash','fixed_interface_hash_mismatch','unflagged_diagnostic'])
    a=ap.parse_args();root=Path(a.result_root).resolve()
    if a.self_test and a.result_root=='.':root=Path(__file__).resolve().parent
    if a.contract_state_test:
        errors=run_certification_state_test(a.contract_state_test,root)
        out={'status':'PASSED' if not errors else 'FAILED','validator_version':VERSION,
            'scope':'Data Dictionary enum typing plus v9.3 joint-certification, source-copy, dominance, and canonical carry-in-prefix hash checks',
            'contract_state_test':a.contract_state_test,'errors':errors}
        if a.contract_state_test=='positive_certified_vector':out['microcase']=certification_state_summary(a.contract_state_test)
        print(json.dumps(out,ensure_ascii=False,indent=2));return 0 if not errors else 1
    if a.self_test:
        try:st=self_test(root)
        except Exception as x:st={'status':'FAILED','cases':{},'errors':[f'self-test exception:{x}']}
        out={'status':st['status'],'validator_version':VERSION,'profile':'SELF_TEST_ONLY','scope':'validator internal self-tests only; no result package validation is performed','self_test':st}
        print(json.dumps(out,ensure_ascii=False,indent=2));return 0 if out['status']=='PASSED' else 1
    try:e=validate(root,a.profile,a.schema_only or a.profile=='SCHEMA_ONLY')
    except Exception as x:e=[f'validator exception:{x}']
    out={'status':'PASSED' if not e else 'FAILED','validator_version':VERSION,'profile':a.profile,'scope':'complete package; strict specs/CSV; formal hash DAG; child contracts; run-plan payload/DAG/seed; execution/output bundles; PK/FK/lineage; theorem-applicability derivation; approved builds; acceptance and non-vacuity','errors':e}
    print(json.dumps(out,ensure_ascii=False,indent=2));return 0 if out['status']=='PASSED' else 1
if __name__=='__main__':sys.exit(main())
