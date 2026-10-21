"""Seven paper figures, with explicit pilot labels and conditional denominators."""
from collections import defaultdict
from fractions import Fraction as F
from pathlib import Path
import statistics
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import common as C
import summarize as S

LABEL={'production_analytic':'AB','seq':'SEQ','no_first_cuts':'Without first history',
 'no_first_work':'Without first workload cut','no_shared_budget':'Without shared budget',
 'completion_only':'Completion only','full':'Full'}
COL={'production_analytic':'#12679B','seq':'#C36124'}

def equal_cell_mean(records,metric):
 """Equal configuration weights, rather than weighting by surviving pair counts."""
 grouped=defaultdict(list)
 for row in records:
  if metric in row:grouped[(row['uc'],row['ue'],row['gap'])].append(row[metric])
 if len(grouped)!=8:return None
 return statistics.mean(statistics.mean(v) for v in grouped.values())

def equal_cell_ci(records,metric,expected):
 """Resample the SAME bones across U_E/G, then average eight cell means."""
 keys=sorted({(c['metadata']['target_uc'],c['metadata']['target_ue'],c['metadata']['harvest_gap']) for c in expected},key=str)
 if len(keys)!=8 or equal_cell_mean(records,metric) is None:return [None,None],0
 index={k:i for i,k in enumerate(keys)};values={r['case_id']:r.get(metric) for r in records}
 strata=defaultdict(dict)
 for c in expected:
  md=c['metadata'];u=md['target_uc'];cluster=md['cluster_id']
  a=strata[u].setdefault(cluster,np.zeros((8,2)))
  value=values.get(c['case_id'])
  if value is not None:a[index[u,md['target_ue'],md['harvest_gap']]]=[value,1]
 reps=C.PROTOCOL['bootstrap_repetitions'];rng=np.random.default_rng(C.PROTOCOL['bootstrap_seed'])
 totals=np.zeros((reps,8,2))
 for u in sorted(strata):
  arr=np.asarray(list(strata[u].values()));pick=rng.integers(0,len(arr),size=(reps,len(arr)))
  totals+=arr[pick].sum(axis=1)
 valid=(totals[:,:,1]>0).all(axis=1)
 if not valid.any():return [None,None],0
 means=(totals[valid,:,0]/totals[valid,:,1]).mean(axis=1)
 return list(map(float,np.percentile(means,[2.5,97.5]))),int(valid.sum())

def plot(output,suite):
 import inputs
 output=Path(output);folder=output/suite;spec=C.read(folder/'plan.json');summary=C.read(folder/'summary.json')
 if C.source_identity()['sha256']!=spec['source_sha256']:raise ValueError('Source changed since analysis')
 if summary['reference_checks_pending'] or summary['invalid_certificates']:raise ValueError('Plot requires successful independent certificate replay')
 if summary['observed_requests']!=summary['planned_requests']:raise ValueError('Plot requires all selected requests; no partial denominator')
 if summary['request_errors']:raise ValueError('Resolve worker errors in every repetition before plotting')
 cases={c['case_id']:c for c in inputs.load(output,C.SUITES[suite][0])}
 cases={cid:cases[cid] for cid,_ in spec['selected']}
 logs=C.record_rows(folder/'primary.jsonl');first={(r['case_id'],r['method']):r for r in logs if r['repeat']==0}
 figdir=folder/'figures';figdir.mkdir(exist_ok=True);saved=[]
 plt.rcParams.update({'font.size':9,'axes.spines.top':False,'axes.spines.right':False,
  'pdf.fonttype':42,'savefig.bbox':'tight','axes.grid':True,'grid.alpha':.18})
 def finish(fig,name,note=None):
  labels=[]
  if spec['pilot'] or spec['custom_run']:labels.append('PILOT / SELECTED INPUTS — NOT FINAL PAPER RESULTS')
  if spec['parallel_timing']:labels.append('PARALLEL TIMING — F2 EXTENSION / '+str(len(spec['cpus']))+' concurrent single-core requests')
  if labels:fig.suptitle('\n'.join(labels),fontsize=9,color='#944')
  if note:fig.text(.5,.005,note,ha='center',fontsize=7)
  fig.tight_layout(rect=(0,.035,1,.91 if len(labels)>1 else (.94 if labels else 1)))
  for ext in ['pdf','png']:
   p=figdir/(name+'.'+ext);fig.savefig(p,dpi=200);saved.append(str(p))
  plt.close(fig)
 def rate(ax,filter_fn,xname,styles):
  for method,extra,label,ls in styles:
   grouped=defaultdict(list)
   for cid,c in cases.items():
    meta=c['metadata']
    if filter_fn(c) and extra(c):grouped[float(F(str(S.cell(c)[xname])))].append((cid,c))
   xs=[];ys=[];low=[];high=[]
   for x,items in sorted(grouped.items()):
    vals=[(c['metadata']['cluster_id'],S.stratum(c,suite),100*int(first[cid,method]['status']=='CERTIFIED'),1) for cid,c in items]
    interval=S.ci(vals)
    xs.append(statistics.mean(float(F(c['metadata']['actual_uc'])) for _,c in items) if xname=='uc' else x)
    ys.append(statistics.mean(v[2] for v in vals));low.append(interval[0]);high.append(interval[1])
   if xs:
    ax.plot(xs,ys,label=label,ls=ls,color=COL[method],marker='o',ms=3)
    ax.fill_between(xs,low,high,color=COL[method],alpha=.09)
  ax.set_ylim(-2,102);ax.set_ylabel('Certified task sets (%)')
 def md(c):return c['metadata']
 if suite in ('main','pilot'):
  fig,axs=plt.subplots(2,2,figsize=(9,6),squeeze=False)
  for row,m in enumerate([2,4]):
   for col,d in enumerate(['implicit','ratio_0.7_1']):
    styles=[(method,lambda c,e=e:F(md(c)['target_ue'])==F(e),LABEL[method]+' / UE='+e,ls)
     for e,ls in [('0.40','-'),('0.70','--')] for method in C.PRODUCTION]
    ax=axs[row,col];rate(ax,lambda c,m=m,d=d:c['model']['processors']==m and md(c)['deadline_mode']==d and md(c)['harvest_gap']==1,'uc',styles)
    ax.set_title(f'M={m}, '+('D=T' if d=='implicit' else 'constrained deadlines'));ax.set_xlabel('CPU utilization UC')
  axs[0,0].legend(fontsize=7);finish(fig,'fig1_cpu_certification','Shading: 95% cluster bootstrap intervals; denominators and paired states are in tables.')
  fig,axs=plt.subplots(1,2,figsize=(9,3.8))
  common=lambda c:c['model']['processors']==4 and md(c)['deadline_mode']=='implicit'
  styles=[(method,lambda c,u=u:F(md(c)['target_uc'])==F(u),LABEL[method]+' / UC='+u,ls)
   for u,ls in [('0.35','-'),('0.50','--')] for method in C.PRODUCTION]
  rate(axs[0],lambda c:common(c) and md(c)['harvest_gap']==1,'ue',styles);axs[0].set_xlabel('Energy utilization UE')
  styles=[(method,lambda c,e=e:F(md(c)['target_ue'])==F(e),LABEL[method]+' / UE='+e,ls)
   for e,ls in [('0.40','-'),('0.70','--')] for method in C.PRODUCTION]
  rate(axs[1],lambda c:common(c) and F(md(c)['target_uc'])==F('.50'),'gap',styles);axs[1].set_xlabel('Harvest gap G (slots)')
  for ax in axs:ax.legend(fontsize=7)
  finish(fig,'fig2_energy_certification','M=4, D=T. Other configurations remain in complete tables.')
  fig,axs=plt.subplots(3,2,figsize=(9,9),squeeze=False)
  for row,(m,d) in enumerate([(2,'implicit'),(2,'ratio_0.7_1'),(4,'ratio_0.7_1')]):
   def condition(c,m=m,d=d):return c['model']['processors']==m and md(c)['deadline_mode']==d
   styles=[(method,lambda c,u=u:F(md(c)['target_uc'])==F(u),LABEL[method]+' / UC='+u,ls)
    for u,ls in [('0.35','-'),('0.50','--')] for method in C.PRODUCTION]
   rate(axs[row,0],lambda c:condition(c) and md(c)['harvest_gap']==1,'ue',styles)
   styles=[(method,lambda c,e=e:F(md(c)['target_ue'])==F(e),LABEL[method]+' / UE='+e,ls)
    for e,ls in [('0.40','-'),('0.70','--')] for method in C.PRODUCTION]
   rate(axs[row,1],lambda c:condition(c) and F(md(c)['target_uc'])==F('.50'),'gap',styles)
   for col in [0,1]:axs[row,col].set_title(f'M={m}, '+('D=T' if d=='implicit' else 'constrained deadlines'))
   axs[row,0].set_xlabel('Energy utilization UE');axs[row,1].set_xlabel('Harvest gap G (slots)')
   for ax in axs[row]:ax.legend(fontsize=6)
  finish(fig,'supp_fig2_other_settings','Companion energy curves for the other processor/deadline settings; same definitions and samples as Fig. 2.')
  fig,ax=plt.subplots(figsize=(7,3.8));groups=defaultdict(list)
  for r in summary['precision']:
   if r['reference']=='seq' and r['gap']==1 and F(r['ue']) in [F('.4'),F('.7')]:groups[float(F(r['uc']))].append(r['response_reduction_percent'])
  if groups:
   keys=sorted(groups);ax.boxplot([groups[k] for k in keys],showfliers=True)
   ax.set_xticks(range(1,len(keys)+1),[str(k) for k in keys])
   for i,k in enumerate(keys,1):ax.text(i,.98,f'n={len(groups[k])}',transform=ax.get_xaxis_transform(),ha='center',va='top',fontsize=7)
  else:ax.text(.5,.5,'No jointly certified task sets',transform=ax.transAxes,ha='center')
  ax.axhline(0,color='black',lw=.8);ax.set_xlabel('CPU utilization UC');ax.set_ylabel('Mean response-bound reduction per set (%)')
  finish(fig,'fig3_response_bounds','AB relative to SEQ, jointly certified sets only, CPU scan A at G=1. Negative values favor SEQ.')
 elif suite=='mechanism':
  fig,axs=plt.subplots(1,3,figsize=(12,3.8));hs=[0,.5,1]
  curves=[]
  for ref in C.MECHANISMS[1:]:
   ys=[[],[],[]];ns=[];intervals=[[],[]]
   for h in hs:
    ids=[cid for cid,c in cases.items() if float(F(md(c)['heterogeneity_h']))==h]
    delta=[(md(cases[cid])['cluster_id'],S.stratum(cases[cid],suite),100*(int(first[cid,'full']['status']=='CERTIFIED')-int(first[cid,ref]['status']=='CERTIFIED')),1) for cid in ids]
    ys[0].append(statistics.mean(t[2] for t in delta) if delta else np.nan)
    interval=S.ci(delta)
    pp=[r for r in summary['precision'] if r['reference']==ref and float(F(r['h']))==h]
    ns.append(len(pp))
    for j,metric in [(1,'response_reduction_percent'),(2,'first_reduction_percent')]:
     value=equal_cell_mean(pp,metric);ys[j].append(value if value is not None else np.nan)
     bounds,valid=equal_cell_ci(pp,metric,[cases[cid] for cid in ids]);intervals[j-1].append((bounds,valid))
    curves.append({'reference':ref,'h':h,'inputs':len(ids),'common_certified':len(pp),
     'certification_difference_pp':ys[0][-1],'certification_ci95':interval,
     'response_equal_cell_mean':None if np.isnan(ys[1][-1]) else ys[1][-1],
     'first_equal_cell_mean':None if np.isnan(ys[2][-1]) else ys[2][-1],
     'response_ci95':intervals[0][-1][0],'response_valid_bootstraps':intervals[0][-1][1],
     'first_ci95':intervals[1][-1][0],'first_valid_bootstraps':intervals[1][-1][1]})
   rr=curves[-len(hs):]
   axs[0].fill_between(hs,[r['certification_ci95'][0] if r['certification_ci95'][0] is not None else np.nan for r in rr],
    [r['certification_ci95'][1] if r['certification_ci95'][1] is not None else np.nan for r in rr],alpha=.08)
   for j in [1,2]:
    lo=[v[0][0] if v[0][0] is not None else np.nan for v in intervals[j-1]]
    hi=[v[0][1] if v[0][1] is not None else np.nan for v in intervals[j-1]]
    axs[j].fill_between(hs,lo,hi,alpha=.08)
   for ax,y in zip(axs,ys):ax.plot(hs,y,marker='o',label=LABEL[ref])
  for ax,ylabel in zip(axs,['Full minus ablation (percentage points)','Response-bound reduction (%)','First-bound reduction (%)']):
   ax.axhline(0,color='black',lw=.7);ax.set_xlabel('Energy heterogeneity h');ax.set_ylabel(ylabel);ax.set_xticks(hs)
  axs[0].legend(fontsize=6)
  S.csvwrite(folder/'tables/mechanism_curves.csv',curves)
  finish(fig,'fig4_mechanisms','F/R: equal weights over 8 configurations; missing points mean insufficient paired coverage. CO uses F=R-C+1; counts are in tables.')
 elif suite=='scale':
  fig,axs=plt.subplots(4,2,figsize=(9,9),gridspec_kw={'height_ratios':[3,1,3,1]})
  for row,u in enumerate(['.30','.50']):
   for col,xname in enumerate(['n','m']):
    for method in C.PRODUCTION:
     items=[r for r in summary['timings'] if r['method']==method and F(r['uc'])==F(u) and (r['m']==4 if xname=='n' else r['n']==2*r['m'])]
     items.sort(key=lambda r:r[xname]);x=[r[xname] for r in items]
     top,bottom=axs[2*row,col],axs[2*row+1,col]
     top.plot(x,[r['conditional_median_seconds'] if r['conditional_median_seconds'] is not None else np.nan for r in items],marker='o',color=COL[method],label=LABEL[method])
     top.plot(x,[r['conditional_P95_seconds'] if r['conditional_P95_seconds'] is not None else np.nan for r in items],ls=':',color=COL[method],alpha=.55)
     bottom.plot(x,[100*r['completed']/r['inputs'] for r in items],marker='o',color=COL[method])
    axs[2*row,col].set_yscale('log');axs[2*row,col].set_ylabel('Completed request time (s)');axs[2*row,col].set_title('UC='+u)
    axs[2*row+1,col].set_ylabel('Done (%)');axs[2*row+1,col].set_ylim(-2,102)
    axs[2*row+1,col].set_xlabel('Tasks N (M=4)' if xname=='n' else 'Processors M (N=2M)')
  axs[0,0].legend(fontsize=7);finish(fig,'fig5_task_scale','Solid: conditional median; dotted: conditional P95. Timeouts remain unresolved; complete counts are in tables.')
 elif suite=='numeric_scale':
  fig,axs=plt.subplots(2,2,figsize=(9,6),sharex=True)
  rr=summary['repeat_stability']
  for col,u in enumerate(['.30','.50']):
   for method in C.PRODUCTION:
    xs=[1,2,4,8];med=[];p95=[];rates=[]
    for factor in xs:
     items=[r for r in rr if r['method']==method and F(r['uc'])==F(u) and r['factor']==factor]
     values=[r['median_seconds'] for r in items if r['stable_completed']]
     med.append(float(np.median(values)) if values else np.nan);p95.append(float(np.percentile(values,95)) if values else np.nan)
     rates.append(100*len(values)/len(items) if items else np.nan)
    label=LABEL[method]+' / UC='+u
    axs[0,col].plot(xs,med,marker='o',color=COL[method],label=LABEL[method])
    axs[0,col].plot(xs,p95,ls=':',color=COL[method],alpha=.55)
    axs[1,col].plot(xs,rates,marker='o',color=COL[method])
   axs[0,col].set_yscale('log');axs[0,col].set_ylabel('Stable request time (s)');axs[0,col].legend(fontsize=7);axs[0,col].set_title('UC='+u)
   axs[1,col].set_xlabel('Time scale factor');axs[1,col].set_ylabel('Three stable completions (%)');axs[1,col].set_xticks([1,2,4,8]);axs[1,col].set_ylim(-2,102)
  finish(fig,'fig6_numeric_scale','Solid: median; dotted: P95, using three stable completions. C/D/T/G scale together; identical schedules are not assumed.')
 elif suite=='timing':
  fig,axs=plt.subplots(1,2,figsize=(9,3.8))
  for ax,family in zip(axs,['main','scale']):
   for method in C.PRODUCTION:
    rr=[r for r in summary['budget_coverage'] if r['family']==family and r['method']==method and r['inputs']]
    ax.plot([r['budget_seconds'] for r in rr],[r['certification_percent'] for r in rr],marker='o',color=COL[method],label=LABEL[method])
   ax.set_title(family+' preselected sample');ax.set_xlabel('Request wall-time budget (s)');ax.set_ylabel('Certified task sets (%)');ax.set_ylim(-2,102);ax.legend()
  finish(fig,'fig7_budget_coverage','First predeclared repetition only. Denominators are 192 main / 48 scale in the complete timing suite.')
 manifest={'suite':suite,'files':saved,'pilot':spec['pilot'],'custom_run':spec['custom_run'],
  'execution_profile':spec['execution_profile'],'parallel_timing':spec['parallel_timing'],
  'analysis_concurrency':len(spec['cpus']),'time_scope':spec['time_scope'],
  'inputs':summary['inputs'],'note':'PDF and PNG are two exports of the same figure; these are not extra independent experiments.'}
 C.write(figdir/'manifest.json',manifest);return manifest
