#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
from typing import Any
import csv, datetime as dt, hashlib, json, math, re, unicodedata
import yaml
VERSION='1.3.10'
ID_ALGORITHM='SHA256_DOMAIN_SEPARATED_CANONICAL_V1_3_10'
class StrictLoader(yaml.SafeLoader):
    pass
def _construct_mapping(loader,node,deep=False):
    out={}
    for kn,vn in node.value:
        key=loader.construct_object(kn,deep=deep)
        if not isinstance(key,str): raise ValueError(f'non-string YAML mapping key: {key!r}')
        if key in out: raise ValueError(f'duplicate YAML key: {key}')
        out[key]=loader.construct_object(vn,deep=deep)
    return out
StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,_construct_mapping)

def read_text_strict(path:Path)->str:
    raw=path.read_bytes()
    if raw.startswith(b'\xef\xbb\xbf'): raise ValueError(f'UTF-8 BOM forbidden: {path.name}')
    if b'\r' in raw: raise ValueError(f'CR/CRLF forbidden; LF required: {path.name}')
    if raw and not raw.endswith(b'\n'): raise ValueError(f'final newline required: {path.name}')
    text=raw.decode('utf-8')
    if unicodedata.normalize('NFC',text)!=text: raise ValueError(f'text not NFC-normalized: {path.name}')
    for ch in text:
        o=ord(ch)
        if o==0 or (o<32 and ch not in {'\n','\t'}): raise ValueError(f'forbidden control character U+{o:04X}: {path.name}')
    return text

def _validate_json_scalar_tree(x:Any,path='root')->None:
    if x is None or isinstance(x,(str,bool,int)): return
    if isinstance(x,float): raise ValueError(f'YAML float forbidden at {path}')
    if isinstance(x,(dt.date,dt.datetime,dt.time)): raise ValueError(f'implicit YAML date/time forbidden at {path}')
    if isinstance(x,list):
        for i,v in enumerate(x): _validate_json_scalar_tree(v,f'{path}[{i}]')
        return
    if isinstance(x,dict):
        for k,v in x.items():
            if not isinstance(k,str): raise ValueError(f'non-string mapping key at {path}')
            _validate_json_scalar_tree(v,f'{path}.{k}')
        return
    raise ValueError(f'non-JSON YAML scalar type {type(x).__name__} at {path}')

def load_yaml_strict(path:Path)->Any:
    text=read_text_strict(path)
    for event in yaml.parse(text):
        if isinstance(event,yaml.events.AliasEvent): raise ValueError(f'YAML aliases forbidden: {path.name}')
        if getattr(event,'anchor',None) is not None: raise ValueError(f'YAML anchors forbidden: {path.name}')
        if getattr(event,'tag',None)=='tag:yaml.org,2002:merge': raise ValueError(f'YAML merge keys forbidden: {path.name}')
    obj=yaml.load(text,Loader=StrictLoader)
    _validate_json_scalar_tree(obj,path.name)
    return obj

def canonical_json_bytes(obj:Any)->bytes:
    def norm(x):
        if isinstance(x,dict):
            for k in x:
                if not isinstance(k,str): raise ValueError('canonical mapping keys must be strings')
            return {k:norm(x[k]) for k in sorted(x,key=lambda z:z.encode('utf-8'))}
        if isinstance(x,list): return [norm(v) for v in x]
        if isinstance(x,str):
            if unicodedata.normalize('NFC',x)!=x: raise ValueError('non-NFC string in canonical object')
            return x
        if x is None or isinstance(x,(bool,int)): return x
        if isinstance(x,float): raise ValueError('float forbidden in canonical object')
        raise ValueError(f'unsupported canonical type: {type(x).__name__}')
    return json.dumps(norm(obj),ensure_ascii=False,sort_keys=False,separators=(',',':'),allow_nan=False).encode('utf-8')

def domain_hash(domain:str,preimage:Any)->str:
    return hashlib.sha256(domain.encode('utf-8')+b'\x00'+canonical_json_bytes(preimage)).hexdigest()
def sha256_file(path:Path)->str: return hashlib.sha256(path.read_bytes()).hexdigest()
def sidecar_path(path:Path)->Path: return Path(str(path)+'.sha256')
def write_sidecar(path:Path)->None: sidecar_path(path).write_text(f'{sha256_file(path)}  {path.name}\n',encoding='utf-8')
def validate_sidecar(path:Path)->None:
    sp=sidecar_path(path)
    if not sp.exists(): raise ValueError(f'missing sidecar: {sp.name}')
    text=read_text_strict(sp).strip();m=re.fullmatch(r'([0-9a-f]{64})  (.+)',text)
    if not m: raise ValueError(f'invalid sidecar format: {sp.name}')
    if m.group(2)!=path.name: raise ValueError(f'sidecar filename mismatch: {sp.name}')
    if m.group(1)!=sha256_file(path): raise ValueError(f'sidecar hash mismatch: {path.name}')
def atomic_write_json_with_sidecar(path:Path,obj:Any)->None:
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    tmp.replace(path);write_sidecar(path)
def set_nested_null(obj:Any,dotted:str)->Any:
    out=json.loads(json.dumps(obj,ensure_ascii=False));cur=out;parts=dotted.split('.')
    for p in parts[:-1]:
        if not isinstance(cur,dict) or p not in cur: raise KeyError(dotted)
        cur=cur[p]
    if not isinstance(cur,dict) or parts[-1] not in cur: raise KeyError(dotted)
    cur[parts[-1]]=None;return out
def get_nested(obj:Any,dotted:str)->Any:
    cur=obj
    for p in dotted.split('.'):
        if not isinstance(cur,dict) or p not in cur: raise KeyError(dotted)
        cur=cur[p]
    return cur
def canonical_object_self_hash(obj:Any,dotted_self_field:str,domain:str)->str:
    return domain_hash(domain,set_nested_null(obj,dotted_self_field))
_INT_RE=re.compile(r'(?:0|-[1-9][0-9]*|[1-9][0-9]*)\Z')
_RAT_RE=re.compile(r'(0|-[1-9][0-9]*|[1-9][0-9]*)/([1-9][0-9]*)\Z')
_TS_RE=re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z')
def parse_canonical_integer(value:str)->int:
    if not _INT_RE.fullmatch(value): raise ValueError(f'noncanonical integer: {value}')
    return int(value)
def parse_canonical_number(value:str)->tuple[int,int]:
    if _INT_RE.fullmatch(value): return int(value),1
    m=_RAT_RE.fullmatch(value)
    if not m: raise ValueError(f'noncanonical number: {value}')
    num,den=int(m.group(1)),int(m.group(2))
    if math.gcd(abs(num),den)!=1: raise ValueError(f'unreduced rational: {value}')
    if num==0: raise ValueError('zero rational must use integer form 0')
    if den==1: raise ValueError('integer rational must use integer form')
    return num,den
def compare_numbers(a:str,b:str)->int:
    an,ad=parse_canonical_number(a);bn,bd=parse_canonical_number(b);x=an*bd-bn*ad
    return -1 if x<0 else (1 if x>0 else 0)
def parse_timestamp(value:str)->dt.datetime:
    if not _TS_RE.fullmatch(value): raise ValueError(f'invalid RFC3339 UTC timestamp: {value}')
    try:return dt.datetime.strptime(value,'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc)
    except ValueError as e: raise ValueError(f'invalid calendar timestamp: {value}') from e

def read_csv_strict(path:Path)->tuple[list[str],list[dict[str,str]]]:
    text=read_text_strict(path)
    if '\n\n' in text: raise ValueError(f'blank CSV line forbidden: {path.name}')
    with path.open('r',newline='',encoding='utf-8') as fh:
        r=csv.DictReader(fh,strict=True);h=r.fieldnames or []
        if len(h)!=len(set(h)): raise ValueError(f'duplicate CSV header: {path.name}')
        rows=[]
        for row in r:
            if None in row: raise ValueError(f'extra CSV columns: {path.name}')
            rows.append(row)
        return h,rows
def canonical_csv_sha256(path:Path)->str:
    read_csv_strict(path)
    return sha256_file(path)
def mask_members(value:str,allowed:list[str])->list[str]:
    if value=='': raise ValueError('empty CSV cell is null, not an empty mask; use EMPTY')
    if value=='EMPTY': return []
    parts=value.split('|')
    if 'EMPTY' in parts: raise ValueError('EMPTY mask sentinel must be used alone')
    if parts!=sorted(set(parts)): raise ValueError(f'mask must be sorted and unique: {value}')
    bad=[p for p in parts if p not in allowed]
    if bad: raise ValueError(f'unknown mask members: {bad}')
    return parts
def format_mask(parts:set[str]|list[str])->str:
    vals=sorted(set(parts))
    return 'EMPTY' if not vals else '|'.join(vals)
def validate_scalar(value:str,spec:dict,enums:dict,masks:dict)->None:
    typ=spec.get('type')
    if typ=='hash_sha256':
        if not re.fullmatch(r'[0-9a-f]{64}',value): raise ValueError('invalid SHA-256')
    elif typ=='integer':
        n=parse_canonical_integer(value)
        if spec.get('minimum') is not None and n<int(spec['minimum']): raise ValueError('below minimum')
        if spec.get('maximum') is not None and n>int(spec['maximum']): raise ValueError('above maximum')
    elif typ=='canonical_number': parse_canonical_number(value)
    elif typ=='boolean':
        if value not in {'false','true'}: raise ValueError('invalid boolean')
    elif typ=='timestamp': parse_timestamp(value)
    elif typ=='identifier':
        if value=='' or unicodedata.normalize('NFC',value)!=value or any(ord(c)<32 for c in value): raise ValueError('invalid identifier')
    elif typ=='enum':
        ref=spec.get('enum_ref')
        if ref not in enums or value not in enums[ref]: raise ValueError(f'invalid enum {ref}')
    elif typ=='enum_set':
        ref=spec.get('enum_ref')
        if ref not in masks: raise ValueError(f'unknown mask ref {ref}')
        mask_members(value,masks[ref])
    else:
        if unicodedata.normalize('NFC',value)!=value: raise ValueError('non-NFC string')
def condition_matches(row:dict[str,str],cond:dict)->bool:
    field=cond.get('field');val=row.get(field,'')
    if 'equals' in cond:return val==str(cond['equals'])
    if 'in' in cond:return val in {str(x) for x in cond['in']}
    if 'not_in' in cond:return val not in {str(x) for x in cond['not_in']}
    if 'nonempty' in cond:return (val!='') is bool(cond['nonempty'])
    raise ValueError(f'unsupported condition: {cond}')
def canonical_pk_string(row:dict[str,str],pk:list[str])->str:
    return json.dumps([row.get(k,'') for k in pk],ensure_ascii=False,separators=(',',':'))
def row_object(row:dict[str,str])->dict[str,Any]:
    return {k:(v if v!='' else None) for k,v in row.items()}
