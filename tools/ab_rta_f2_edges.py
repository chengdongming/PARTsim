#!/usr/bin/env python3
"""Incremental positive-utilization endpoint experiment for the frozen F2 RTA.

Upload this one file beside ab_rta_autodl.py and run: python ab_rta_f2_edges.py start
The original checkout, environment and results are read only. Python >= 3.11.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from fractions import Fraction as F
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
import zipfile

sys.dont_write_bytecode = True

SCHEMA = 'AB_RTA_F2_EDGES_20261022_1'
CORE_SOURCE = 'f0fb5bdc6f9f847d8fa70eeb39be77a6ec4cadbebdfee2850ba52dc1621c1014'
CORE_COMMIT = '1563ec7d63f46a059c41314d8ca1449ff0933e3f'
PACKAGES = {'PyYAML':'6.0.3','numpy':'2.3.5','matplotlib':'3.10.8'}
DEFAULT_BASE = Path('/root/autodl-tmp/ab_rta_f2')
DEFAULT_ROOT = Path('/root/autodl-tmp/ab_rta_f2_edges')
METHODS = ['production_analytic','seq']
EDGE = {'cpu_targets':['0.05','1.00'], 'energy_targets':['0.05','1.00'],
        'processors':[2,4], 'cpu_scan_energy':['0.40','0.70'],
        'energy_scan_cpu':['0.35','0.50'], 'deadlines':['implicit','ratio_0.7_1'],
        'samples_per_cell':100, 'gap':1, 'budget_seconds':30, 'repetitions':1,
        'axis_limits':[0,1], 'zero_points':'excluded: C >= 1 and power > 0',
        'upper_cpu_rule':'reject rounding overshoots before analysis; candidate index i + 100*k',
        'old_results':'read only; original 9600-input estimates remain separate'}
# Regenerated and checked before release; applies to all 3,200 formal inputs.
EXPECTED_INPUT_IDENTITY = '81f67635fc27ccc4456f39ea392dd45b3bc2f74a16b8fcd5d965ab46a84c12d6'


def now(): return datetime.now(timezone.utc).isoformat()
def canonical(x): return json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
def digest(x): return hashlib.sha256(canonical(x).encode()).hexdigest()
def file_sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path,value,immutable=False):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if immutable and path.exists():
        if canonical(read(path))!=canonical(value): raise RuntimeError(f'已有配置不同，拒绝覆盖：{path}')
        return
    tmp=path.with_name(path.name+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    os.replace(tmp,path)


def positive_target(value):
    v=F(value)
    if not 0<v<=1: raise ValueError('利用率必须满足 0 < u <= 1；0不是当前正工作量、正能耗模型的合法输入。')
    return v


def disjoint(base,root):
    base,root=Path(base).resolve(),Path(root).resolve()
    if base==root or base in root.parents or root in base.parents:
        raise RuntimeError('补测目录必须与原实验目录分开，不能覆盖原代码、环境或结果。')


def load_core(repo):
    repo=Path(repo).resolve();folder=repo/'experiments/asapblock_f2'
    sys.path.insert(0,str(folder))
    C=importlib.import_module('common')
    if C.HERE.resolve()!=folder: raise RuntimeError('导入了其他F2目录，停止运行。')
    if C.source_identity()['sha256']!=CORE_SOURCE: raise RuntimeError('原RTA源码摘要与固定F2版本不同，停止运行。')
    return C,importlib.import_module('inputs'),importlib.import_module('run'),importlib.import_module('summarize')


def environment(C,smoke=False):
    try: return C.environment()
    except (FileNotFoundError,PermissionError):
        if not smoke: raise RuntimeError('正式运行需能读取Linux CPU和内存信息；当前环境不支持，未启动正式实验。') from None
        cpus=sorted(os.sched_getaffinity(0))
        return {'python':platform.python_version(),'platform':platform.platform(),
            'affinity':cpus,'physical_cpus':cpus,'cpu_quota':None,'memory_max':None,
            'system_memory_bytes':None,'cpu_model':None,'git_head':None,
            'packages':{n:importlib.metadata.version(n) for n in PACKAGES},
            'partial_environment':True,'scope':'SMOKE_ONLY; unavailable host fields are not fabricated'}


def verify_base(C,base_results,env,smoke=False):
    base=Path(base_results);p=read(base/'main/plan.json');s=read(base/'main/summary.json')
    gate=read(base/'preflight_checks.json');execution=read(base/'main/execution.json')
    if p['source_sha256']!=CORE_SOURCE or gate.get('source_sha256')!=CORE_SOURCE or gate.get('status')!='PASS':
        raise RuntimeError('原实验代码或自检记录与冻结版本不一致。')
    if p.get('custom_run') or p.get('pilot') or p['methods']!=METHODS or p['budget_seconds']!=30 or p['repetitions']!=1:
        raise RuntimeError('原main不是完整冻结的正式主实验。')
    if s['inputs']!=9600 or s['observed_requests']!=19200 or not execution['complete']:
        raise RuntimeError('原主实验未完成，不能与边界补测合并展示。')
    if s['reference_checks_pending'] or s['invalid_certificates'] or s['request_errors']:
        raise RuntimeError('原主实验存在未核验或错误记录。')
    oldenv={k:v for k,v in p['environment'].items() if k!='git_head'}
    newenv={k:v for k,v in env.items() if k!='git_head'}
    same=oldenv==newenv
    if not smoke:
        state=read(base.parent/'run_state.json')
        if state.get('state')!='COMPLETE':raise RuntimeError('请等原F2完整批次COMPLETE后再启动补测。')
        if env['packages']!=PACKAGES: raise RuntimeError('原Python依赖已变化，请恢复原venv；不要升级依赖。')
        if not same: raise RuntimeError('当前硬件或依赖与原主实验不同，停止正式补测，避免混合不同测量条件。')
    return {'source_sha256':CORE_SOURCE,'main_run_id':p['run_id'],
        'main_plan_sha256':file_sha(base/'main/plan.json'),
        'main_summary_sha256':file_sha(base/'main/summary.json'),
        'preflight_sha256':file_sha(base/'preflight_checks.json'),
        'same_environment':same,'smoke_only':smoke}


def upper_bounded_bone(I,m,uc,index):
    """An input-legality rejection, never a selection by certification outcome."""
    for rejected in range(1000):
        b=I.bone('main',m,2*m,uc,index+100*rejected)
        actual=sum(F(t['C'],t['T']) for t in b['tasks'])/m
        if actual<=1:return b,rejected
    raise RuntimeError('CPU端点合法输入生成次数超过预设上限。')


def build_cases(C,I,base_plan,samples=100):
    if not 1<=samples<=100: raise ValueError('samples outside the frozen range')
    old=dict(base_plan['selected']);cases=[];anchors=0;rejections=Counter()
    for m in EDGE['processors']:
        for uc in EDGE['cpu_targets']:
            positive_target(uc)
            for index in range(samples):
                b,rejected=upper_bounded_bone(I,m,uc,index)
                rejections[f'm{m}:uc{F(uc)}']+=rejected
                for ue in EDGE['cpu_scan_energy']:
                    for d in EDGE['deadlines']:
                        cases.append(I.case(b,ue,1,d,family='boundary_cpu'))
        for uc in EDGE['energy_scan_cpu']:
            for index in range(samples):
                b=I.bone('main',m,2*m,uc,index)
                # Prove reuse of the same old bones; no result-dependent selection.
                for d in EDGE['deadlines']:
                    anchor=I.case(b,'0.40',1,d)
                    if old.get(anchor['case_id'])!=anchor['input_sha256']:
                        raise RuntimeError('能量补测骨架与原主实验不一致。')
                    anchors+=1
                for ue in EDGE['energy_targets']:
                    positive_target(ue)
                    for d in EDGE['deadlines']:
                        cases.append(I.case(b,ue,1,d,family='boundary_energy'))
    if len(cases)!=32*samples or len({c['case_id'] for c in cases})!=len(cases):
        raise RuntimeError('补测输入数量或身份重复。')
    identity=C.digest(sorted((c['case_id'],c['input_sha256']) for c in cases))
    if samples==100 and EXPECTED_INPUT_IDENTITY is not None and identity!=EXPECTED_INPUT_IDENTITY:
        raise RuntimeError('补测输入与发布前冻结的身份摘要不一致。')
    return cases,{'inputs':len(cases),'identity':identity,'anchored_old_cases':anchors,
        'clusters':len({c['metadata']['cluster_id'] for c in cases}),
        'zero_points_excluded':True,'samples_per_cell':samples,
        'rounding_overshoot_rejections':dict(rejections),'upper_cpu_rule':EDGE['upper_cpu_rule']}


def smoke_selection(C,cases):
    groups=defaultdict(list)
    for c in cases:
        md=c['metadata'];axis=md['family'];u=md['target_uc'] if axis=='boundary_cpu' else md['target_ue']
        groups[axis,u,c['model']['processors']].append(c)
    return [min(v,key=lambda c:C.digest(['edge-smoke',c['case_id']])) for _,v in sorted(groups.items())]


def repair_journal(C,path):
    """Recover only a torn final index line from matching atomic row copies."""
    path=Path(path)
    if not path.exists(): return False
    raw=path.read_bytes()
    if not raw or raw.endswith(b'\n'): return False
    complete=[];lines=raw.splitlines()
    for i,line in enumerate(lines):
        try: complete.append(json.loads(line))
        except (json.JSONDecodeError,UnicodeDecodeError):
            if i!=len(lines)-1: raise RuntimeError('日志中段损坏，拒绝自动恢复。') from None
    copies=[C.read(p) for p in path.with_suffix('.records').glob('*.json')]
    by={r['request_id']:r for r in copies}
    if not copies or len(by)!=len(copies) or len({r['request_id'] for r in complete})!=len(complete):
        raise RuntimeError('无法从独立记录安全恢复日志。')
    if any(r['request_id'] not in by or C.canonical(r)!=C.canonical(by[r['request_id']]) for r in complete):
        raise RuntimeError('完整日志行与独立记录冲突，拒绝覆盖。')
    backup=path.with_name(path.name+'.interrupted_'+hashlib.sha256(raw).hexdigest()[:12])
    if backup.exists() and backup.read_bytes()!=raw: raise RuntimeError('恢复备份存在冲突。')
    backup.write_bytes(raw)
    temp=path.with_name(path.name+'.recovered')
    with temp.open('w',encoding='utf-8') as f:
        for row in sorted(copies,key=lambda r:r['request_id']): f.write(C.canonical(row)+'\n')
        f.flush();os.fsync(f.fileno())
    os.replace(temp,path);return True


def merge_plot_data(base,edge,base_identity):
    # This is a new augmented cohort; the old aggregate result is never replaced.
    old=base['repeat_stability'];new=edge['repeat_stability']
    if set(r['case_id'] for r in old)&set(r['case_id'] for r in new): raise RuntimeError('新旧绘图输入重复。')
    rows=old+new
    seen=set()
    for r in rows:
        key=(r['m'],r['n'],r['uc'],r['ue'],r['deadline'],r['gap'],r['method'],r['cluster_id'])
        if key in seen: raise RuntimeError('同一配置和方法被重复计入曲线。')
        if r['states'][0] not in {'CERTIFIED','NO_CERTIFICATE','TIMEOUT'}: raise RuntimeError('曲线含错误或未核验状态。')
        seen.add(key)
    return {'schema':SCHEMA,'base_identity':base_identity,'old_inputs':base['inputs'],'new_inputs':edge['inputs'],
        'rows':rows,'precision':base['precision']+edge['precision'],
        'scope':'Augmented curves only. Original 9600-input aggregate estimates remain separate.'}


def make_figures(C,S,plot,folder):
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    plt.rcParams.update({'font.family':'DejaVu Serif','font.size':8,'mathtext.fontset':'stix',
        'axes.labelsize':9,'axes.titlesize':9,'legend.fontsize':7,'pdf.fonttype':42,
        'axes.spines.top':False,'axes.spines.right':False,'axes.linewidth':.65})
    colors={'production_analytic':'#245E83','seq':'#B05E35'};names={'production_analytic':'AB','seq':'SEQ'}
    points={};rows=plot['rows']
    def curve(ax,key,xkey,method,predicate,label,marker='o'):
        groups=defaultdict(list)
        for r in rows:
            if r['method']==method and predicate(r):groups[float(F(str(r[xkey])))].append(r)
        xx=[];yy=[];lo=[];hi=[]
        for x,rr in sorted(groups.items()):
            if len(rr)!=100 and not plot.get('smoke_only'): raise RuntimeError('正式曲线每点必须为100个输入。')
            values=[(r['cluster_id'],(r['m'],r['uc']),100*int(r['states'][0]=='CERTIFIED'),1) for r in rr]
            y=sum(v[2] for v in values)/len(values);ci=S.ci(values)
            xx.append(x);yy.append(y);lo.append(y-ci[0]);hi.append(ci[1]-y)
            points.setdefault(key,[]).append({'method':method,'label':label,'x':x,'n':len(rr),'estimate':y,'ci95':ci})
        ax.errorbar(xx,yy,yerr=np.array([lo,hi]),color=colors[method],ls='-' if method==METHODS[0] else '--',
            marker=marker,ms=3.3,mfc='white' if marker=='^' else colors[method],lw=1.1,elinewidth=.6,capsize=1.4,label=label)
    def axis(ax,rate=True):
        ax.set_ylim(-2,102);ax.set_yticks([0,20,40,60,80,100]);ax.grid(axis='y',lw=.45,alpha=.25)
        ax.set_ylabel('Certified task sets (%)')
        if rate: ax.set_xlim(0,1);ax.set_xticks(np.linspace(0,1,6))
    def save(fig,name,legend=True):
        if legend:
            hh,ll=fig.axes[0].get_legend_handles_labels()
            fig.legend(hh,ll,loc='upper center',ncol=4,frameon=False)
        fig.tight_layout(rect=(0,0,1,.91) if legend else (0,0,1,1),pad=.6,w_pad=1.4,h_pad=1.7)
        for ext in ['png','pdf','svg']: fig.savefig(folder/(name+'.'+ext),dpi=300,bbox_inches='tight',pad_inches=.04)
        plt.close(fig)
    fig,axs=plt.subplots(2,2,figsize=(7.1,4.65))
    for i,m in enumerate([2,4]):
        for j,d in enumerate(EDGE['deadlines']):
            ax=axs[i,j]
            for ue,marker in [('2/5','o'),('7/10','^')]:
                for method in METHODS:
                    curve(ax,f'cpu:{m}:{d}','uc',method,lambda r,m=m,d=d,u=ue:r['m']==m and r['deadline']==d and r['ue']==u and r['gap']==1,
                        names[method]+r', $U_E='+f'{float(F(ue)):.2f}'+r'$',marker)
            axis(ax);ax.set_xlabel(r'Target CPU utilization $U_C$')
            ax.set_title(r'$m='+str(m)+r'$, '+(r'$D=T$' if d=='implicit' else r'$0.7T\leq D\leq T$'))
    save(fig,'fig1_cpu_full_axis')
    def energy_panel(a,b,m,d,key):
        for uc,marker in [('7/20','o'),('1/2','^')]:
            for method in METHODS:
                curve(a,key+':energy','ue',method,lambda r,m=m,d=d,u=uc:r['m']==m and r['deadline']==d and r['uc']==u and r['gap']==1,
                    names[method]+r', $U_C='+f'{float(F(uc)):.2f}'+r'$',marker)
        for ue,marker in [('2/5','o'),('7/10','^')]:
            for method in METHODS:
                curve(b,key+':gap','gap',method,lambda r,m=m,d=d,u=ue:r['m']==m and r['deadline']==d and r['uc']=='1/2' and r['ue']==u,
                    names[method]+r', $U_E='+f'{float(F(ue)):.2f}'+r'$',marker)
        axis(a);axis(b,False);a.set_xlabel(r'Energy utilization $U_E$');b.set_xlabel(r'Harvest gap $G$ (slots)');b.set_xticks([1,5,10])
    fig,axs=plt.subplots(1,2,figsize=(7.1,2.9));energy_panel(*axs,4,'implicit','energy4')
    axs[0].set_title(r'Energy demand, $G=1$');axs[1].set_title(r'Supply intermittency, $U_C=0.50$')
    for ax in axs:ax.legend(loc='best',frameon=False,fontsize=6.7)
    save(fig,'fig2_energy_full_axis',False)
    fig,axs=plt.subplots(3,2,figsize=(7.1,6.8))
    for i,(m,d) in enumerate([(2,'implicit'),(2,'ratio_0.7_1'),(4,'ratio_0.7_1')]):
        energy_panel(*axs[i],m,d,f's1:{i}')
        for ax in axs[i]:
            ax.set_title(r'$m='+str(m)+r'$, '+(r'$D=T$' if d=='implicit' else r'$0.7T\leq D\leq T$'))
            ax.legend(loc='best',frameon=False,fontsize=6.1)
    save(fig,'supp1_energy_full_axis',False)
    fig,axs=plt.subplots(1,2,figsize=(7.1,2.9))
    for m,ax in zip([2,4],axs):
        rr=[r for r in plot['precision'] if r['m']==m and r['gap']==1 and r['ue'] in ['2/5','7/10']]
        allv=[r['response_reduction_percent'] for r in rr]
        for uc in [.05,.2,.35,.5,.65,.8,1.]:
            vv=[r['response_reduction_percent'] for r in rr if float(F(r['uc']))==uc]
            points.setdefault('precision:'+str(m),[]).append({'x':uc,'n':len(vv),'values':vv})
            if len(vv)>=5:
                ax.boxplot([vv],positions=[uc],widths=.045,manage_ticks=False,patch_artist=True,
                    boxprops={'facecolor':'#E2EBF1','edgecolor':colors[METHODS[0]]},
                    medianprops={'color':colors[METHODS[0]]},flierprops={'markersize':2})
            elif vv:ax.scatter(np.linspace(uc-.008,uc+.008,len(vv)),vv,s=9,facecolors='none',edgecolors=colors[METHODS[0]])
            ax.text(uc,.99,'n='+str(len(vv)),transform=ax.get_xaxis_transform(),ha='right' if uc==1 else 'center',va='top',fontsize=6)
        ax.set_xlim(0,1);ax.set_xticks(np.linspace(0,1,6));ax.set_ylim(min(-2,min(allv,default=0)-2),max(allv,default=0)+8)
        ax.axhline(0,color='#555',ls=':',lw=.7);ax.grid(axis='y',alpha=.2,lw=.45)
        ax.set_xlabel(r'Target CPU utilization $U_C$');ax.set_ylabel('Response-bound reduction (%)');ax.set_title(r'$m='+str(m)+r'$')
    save(fig,'fig3_precision_full_axis',False)
    write(folder/'plotted_points.json',points)


def plan():
    return {'schema':SCHEMA,'cpu_new_inputs':1600,'energy_new_inputs':1600,'new_inputs':3200,
        'new_analysis_requests':6400,'old_requests_to_rerun':0,'samples_per_cell':100,
        'primary_budget_seconds':30,'methods':METHODS,'parameters':EDGE,
        'note':'0 is an axis tick only. 0.05 is stored and plotted as 0.05. 1.00 is actually tested.'}


def run_experiment(repo,base_results,root,workers,smoke=False):
    C,I,R,S=load_core(repo);env=environment(C,smoke);base_results=Path(base_results).resolve();root=Path(root).resolve()
    if root==base_results or root in base_results.parents or base_results in root.parents:
        raise RuntimeError('补测输出与原结果目录必须完全分开。')
    base_identity=verify_base(C,base_results,env,smoke)
    import execution
    allocation=execution.allocate(env,workers);original_env=C.environment
    old_protocol=C.PROTOCOL;old_suites=dict(C.SUITES);old_core_suites=list(C.CORE_SUITES)
    C.environment=lambda:env
    C.PROTOCOL={**C.PROTOCOL,'schema':SCHEMA,'endpoint_extension':EDGE,
        'extension_script_sha256':file_sha(__file__),'smoke_only':smoke}
    C.SUITES['edges']=('edges',METHODS,30,1,False)
    C.CORE_SUITES=list(C.CORE_SUITES)+['edges']
    root.mkdir(parents=True,exist_ok=True)
    try:
        with R.lock(root/'worker.lock'):
            frozen={'schema':SCHEMA,'extension_script_sha256':file_sha(__file__),'base_identity':base_identity,
                'environment':{k:v for k,v in env.items() if k!='git_head'},'allocation':allocation,
                'parameters':EDGE,'smoke_only':smoke}
            write(root/'frozen_run.json',frozen,immutable=True)
            def stage(name,**more):write(root/'run_state.json',{'state':'RUNNING','stage':name,'updated_utc':now(),**more})
            stage('PREPARING_INPUTS')
            if not smoke and EXPECTED_INPUT_IDENTITY is None:raise RuntimeError('发布前尚未冻结边界输入摘要。')
            cases,manifest=build_cases(C,I,read(base_results/'main/plan.json'))
            if smoke:
                cases=smoke_selection(C,cases)
                manifest={**manifest,'full_identity':manifest['identity'],'identity':C.digest(sorted((c['case_id'],c['input_sha256']) for c in cases)),
                    'inputs':len(cases),'smoke_only':True,'selection':'minimum fixed hash per axis, endpoint and m'}
            write(root/'input_manifest.json',manifest,immutable=True)
            write(root/'generation_diagnostics.json',I.GENERATION_DIAGNOSTICS,immutable=True)
            C.write(root/'inputs/edges.json.gz',{'cases':cases,'identity':manifest['identity']},immutable=True)
            gate=read(base_results/'preflight_checks.json')
            write(root/'preflight_checks.json',{**gate,'inherited_from':str(base_results/'preflight_checks.json'),
                'inherited_source_unchanged':True,'endpoint_generation_checks':'PASS'},immutable=True)
            write(root/'protocol.json',C.PROTOCOL,immutable=True)
            args=SimpleNamespace(workers=workers,timing_workers=workers,verification_workers=workers,limit=len(cases) if smoke else None)
            with R.lock(root/'edges/run.lock'):
                repair_journal(C,root/'edges/primary.jsonl');repair_journal(C,root/'edges/verification.jsonl')
            stage('ANALYSIS',planned_requests=len(cases)*2)
            R.run_suite(root,'edges',args)
            stage('INDEPENDENT_VERIFICATION',planned_requests=len(cases)*2)
            R.verify(root,'edges',args)
            stage('SUMMARIZING',planned_requests=len(cases)*2)
            S.summarize(root,'edges')
            edge=read(root/'edges/summary.json')
            if edge['request_errors'] or edge['reference_checks_pending'] or edge['invalid_certificates']:
                raise RuntimeError('补测尚有错误或未核验证书，未生成正式合并图。')
            stage('PLOTTING',planned_requests=len(cases)*2)
            if not smoke:
                merged=merge_plot_data(read(base_results/'main/summary.json'),edge,base_identity)
                write(root/'merged/plot_data.json',merged)
                make_figures(C,S,merged,root/'merged/figures')
            write(root/'RESULTS_README.txt',{'scope':'SMOKE_NOT_FORMAL' if smoke else 'FORMAL_INCREMENTAL_BOUNDARY',
                'old_main_inputs':9600,'new_inputs':len(cases),'old_results_unchanged':True,
                'zero_points':'CPU=0 and energy=0 excluded because C >= 1 and power > 0.',
                'plots':'Axes 0 to 1; first actual new point 0.05; upper endpoint 1.00 is measured.',
                'aggregate':'Do not silently replace old 9600-input aggregate rates with new-grid aggregate rates.'})
            write(root/'run_state.json',{'state':'COMPLETE','stage':'COMPLETE','finished_utc':now(),
                'completed_requests':len(cases)*2,'planned_requests':len(cases)*2,'smoke_only':smoke})
            return edge
    except BaseException as exc:
        write(root/'run_state.json',{'state':'FAILED','updated_utc':now(),'error':str(exc),'smoke_only':smoke})
        raise
    finally:
        C.environment=original_env;C.PROTOCOL=old_protocol
        C.SUITES.clear();C.SUITES.update(old_suites);C.CORE_SUITES=old_core_suites


def session_name(root):return 'abedge_'+hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:12]
def active(root):
    return bool(shutil.which('tmux')) and subprocess.run(['tmux','has-session','-t','='+session_name(root)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0


def launch(args):
    base,root=args.base_root.resolve(),args.root.resolve();disjoint(base,root)
    if active(root):print('补测已在后台运行。');return
    py=base/'venv/bin/python';repo=base/'PARTsim';base_results=base/'results'
    if not py.is_file() or not (repo/'experiments/asapblock_f2/common.py').is_file():
        raise RuntimeError('未找到原F2环境。请保留原目录，或用--base-root指定它；无需重新安装。')
    if not shutil.which('tmux'):raise RuntimeError('没有找到tmux，请在原AutoDL环境中运行。')
    root.mkdir(parents=True,exist_ok=True)
    # A small stdlib lock protects repeated start/resume clicks without importing packages.
    import fcntl
    with (root/'launch.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('另一个启动命令正在执行，请稍后查看status。') from None
        if active(root):print('补测已在后台运行。');return
        controller=root/'controller.py';sha=file_sha(__file__)
        if controller.exists() and file_sha(controller)!=sha:raise RuntimeError('此目录已冻结为另一脚本版本，请使用新的--root。')
        old=read(root/'install.json') if (root/'install.json').exists() else None
        workers=args.workers if args.workers is not None else (old['workers'] if old else 28)
        config={'schema':SCHEMA,'base_root':str(base),'repo':str(repo),'base_results':str(base_results),
            'workers':workers,'extension_script_sha256':sha}
        write(root/'install.json',config,immutable=True)
        if not controller.exists():shutil.copyfile(__file__,controller)
        if (root/'run_state.json').exists() and read(root/'run_state.json').get('state')=='COMPLETE':
            print('补测已经完成；可执行pack打包。');return
        argv=[str(py),'-u',str(controller),'_worker','--root',str(root)]
        shell=shlex.join(argv)+' >> '+shlex.quote(str(root/'console.log'))+' 2>&1'
        subprocess.run(['tmux','new-session','-d','-s',session_name(root),shell],check=True)
    print('已启动增量补测：3200个新配置、6400次分析，每次30秒，默认最多28路并发。')
    print('原F2代码、环境及结果保留；无需git pull或重新安装。')
    print('结果目录：'+str(root));print('查看进度：python '+str(Path(__file__).resolve())+' status --root '+str(root))


def status(root):
    root=Path(root);state=read(root/'run_state.json') if (root/'run_state.json').exists() else {'state':'NOT_STARTED'}
    running=active(root)
    if running:state['state']='RUNNING'
    elif state['state']=='RUNNING':state['state']='INTERRUPTED'
    path=root/'edges/primary.jsonl';done=0;counts=Counter()
    if path.exists():
        for line in path.read_text().splitlines():
            try:r=json.loads(line)
            except json.JSONDecodeError:continue
            done+=1;counts[r['status']]+=1
    planned=state.get('planned_requests',6400)
    return {**state,'background_session_alive':running,'completed_requests':done,'planned_requests':planned,
        'percent':round(100*done/planned,2),'analysis_states':dict(counts),'root':str(root),
        'note':'分析100%后还会独立核验和作图；以state=COMPLETE为完成标志。'}


def pack(root):
    root=Path(root)
    if active(root) or read(root/'run_state.json').get('state')!='COMPLETE':raise RuntimeError('请在补测COMPLETE后打包。')
    import fcntl
    with (root/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('实验仍占用目录，未打包。') from None
        target=root/'F2_edges_results_slim.zip'
        include=['controller.py','install.json','frozen_run.json','protocol.json','input_manifest.json','preflight_checks.json','generation_diagnostics.json',
            'run_state.json','RESULTS_README.txt','console.log','edges/plan.json','edges/sources.json','edges/environment.json',
            'edges/execution.json','edges/verification_plan.json','edges/primary.jsonl','edges/verification.jsonl','edges/summary.json']
        files=[root/p for p in include if (root/p).is_file()]
        files+=list((root/'edges/tables').glob('*.csv'))+list((root/'merged').rglob('*'))
        with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
            for p in sorted(set(files)):
                if p.is_file():z.write(p,p.relative_to(root))
        with zipfile.ZipFile(target) as z:
            if z.testzip() is not None:raise RuntimeError('压缩包CRC检查失败。')
    print(str(target));print(f'大小：{target.stat().st_size/1024**2:.2f} MiB；完整证书与输入仍保留在补测目录。')
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['plan','start','resume','status','logs','pack','smoke','_worker'])
    parser.add_argument('--base-root',type=Path,default=DEFAULT_BASE)
    parser.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    parser.add_argument('--workers',type=int,help='default: 28 for formal start; saved value on resume; 4 for smoke')
    parser.add_argument('--repo',type=Path,help='smoke only: local frozen checkout')
    parser.add_argument('--base-results',type=Path,help='smoke only: local completed F2 results')
    args=parser.parse_args()
    for name in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:
        os.environ[name]='1'
    if args.workers is not None and args.workers<1:parser.error('--workers必须大于0')
    if args.action=='plan':print(json.dumps(plan(),ensure_ascii=False,indent=2))
    elif args.action in ['start','resume']:launch(args)
    elif args.action=='status':
        s=status(args.root);print(f'状态：{s["state"]} | 阶段：{s.get("stage","准备中")} | 已完成：{s["completed_requests"]}/{s["planned_requests"]} 次分析 | 进度：{s["percent"]:.2f}%')
        if s.get('error'):print('原因：'+s['error'])
    elif args.action=='logs':
        p=args.root/'console.log';print('\n'.join(p.read_text().splitlines()[-35:]) if p.exists() else '暂无日志')
    elif args.action=='pack':pack(args.root)
    elif args.action=='smoke':
        if args.repo is None or args.base_results is None:parser.error('smoke需要--repo和--base-results')
        edge=run_experiment(args.repo,args.base_results,args.root,args.workers or 4,True)
        print(json.dumps({'status':'SMOKE_PASS','inputs':edge['inputs'],'requests':edge['observed_requests'],
            'methods':edge['methods'],'formal_results':False},ensure_ascii=False))
    else:
        config=read(args.root/'install.json')
        try:
            if config['extension_script_sha256']!=file_sha(__file__):raise RuntimeError('后台脚本与冻结版本不同。')
            run_experiment(Path(config['repo']),Path(config['base_results']),args.root,config['workers'])
        except BaseException as exc:
            write(args.root/'run_state.json',{'state':'FAILED','updated_utc':now(),'error':str(exc)})
            raise


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('已中断。已保存的请求保留；使用resume续跑。',file=sys.stderr);raise SystemExit(130)
    except Exception as exc:print('错误：'+str(exc),file=sys.stderr);raise SystemExit(1)
