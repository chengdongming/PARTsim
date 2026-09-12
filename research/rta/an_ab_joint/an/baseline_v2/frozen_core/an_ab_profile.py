"""Exploratory AN RTA under the existing AB no-overflow/E(release)>=E0 model.

Only the prefix supply is E0+beta(s). A restarted suffix still receives beta(ell).
There is no physical capacity parameter in the no-overflow account. Workload,
processor progress, phase energy and suffix energy use the frozen AN analyzer.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent/'source_an'))
from tightened_rta import Task, check
from suffix_restart_rta import SuffixAnalyzer


class ProfileAnalyzer(SuffixAnalyzer):
    def __init__(self, tasks, cores, release_energy, residence=None):
        check(isinstance(release_energy,int) and release_energy>=0,'invalid release energy')
        self.release_energy=release_energy
        self.residence=None if residence is None else tuple(residence)
        if self.residence is not None:
            check(len(self.residence)==len(tasks),'residence length')
            check(all(t.C<=r<=t.D for t,r in zip(tasks,self.residence)),'uncertified residence domain')
        super().__init__(tasks,cores,True)

    def _work(self,i,length):
        if self.residence is None:
            return super()._work(i,length)
        if length==0:
            return 0
        task=self.tasks[i];r=self.residence[i];values=[0]
        for index,release in enumerate(range(1-r,length)):
            overlap=max(0,min(length,release+r)-max(0,release))
            earlier=values[max(0,index+1-task.T)]
            values.append(max(values[-1],earlier+min(task.C,overlap)))
        return values[-1]

    def candidate_profile(self,k,beta):
        check(len(beta)>self.tasks[k].D,'insufficient beta horizon')
        tail=None
        suffix_checked=0

        def suffix_point(s):
            nonlocal tail,suffix_checked
            if tail is not None:
                return tail<=s
            for ell in range(suffix_checked+1,s+1):
                suffix_checked=ell
                if beta[ell]>=self.suffix_energy(k,ell-1):
                    tail=ell
                    return True
            return False

        def point(q,h):
            s=h+q-1
            if tail is not None and tail<=s:
                return {'branch':'suffix','ell':tail}
            energy=self.phase(k,q,h)
            if energy is None or energy<=self.release_energy+beta[s]:
                return {'branch':'prefix','energy':energy,'supply':self.release_energy+beta[s]}
            if suffix_point(s):
                return {'branch':'suffix','ell':tail}
            return None

        for w in range(self.tasks[k].C,self.tasks[k].D+1):
            progress=self.progress(k,w)
            if progress>w:
                continue
            limit=w-progress
            previous=0;budgets=[];points=[]
            for q in range(1,progress+1):
                for h in range(previous,limit+1):
                    detail=point(q,h)
                    if detail is not None:
                        previous=h;budgets.append(h);points.append(detail)
                        break
                else:
                    break
            if len(budgets)==progress:
                return {'R':w,'A':progress,'h':budgets,'checkpoints':points}
        return None

    def analyze_profile(self,beta):
        answers=[]
        for k in range(len(self.tasks)):
            answer=self.candidate_profile(k,beta)
            answers.append(answer)
            if answer is None:
                return answers+[None]*(len(self.tasks)-len(answers))
        return answers


def analyze(tasks,cores,beta,release_energy,refine=True):
    engine=ProfileAnalyzer(tasks,cores,release_energy)
    try:
        answers=engine.analyze_profile(beta)
    finally:
        engine.clear()
    if not all(answers):
        failure=next(i for i,x in enumerate(answers) if x is None)
        return {'taskset_proven':False,'status':'NO_CANDIDATE','first_unproven_task_1based':failure+1,
                'initial_answers':answers,'initial_bounds':None,'final_bounds':None,'iterations':[]}
    initial=[x['R'] for x in answers]
    previous=tuple(initial)
    iterations=[]
    if refine:
        for _ in range(1+sum(r-t.C for r,t in zip(previous,tasks))):
            engine=ProfileAnalyzer(tasks,cores,release_energy,previous)
            try:
                updated=engine.analyze_profile(beta)
            finally:
                engine.clear()
            check(all(updated),'refinement lost a jointly valid certificate')
            current=tuple(x['R'] for x in updated)
            check(all(t.C<=r<=old<=t.D for t,r,old in zip(tasks,current,previous)),
                  'refinement violates nonincreasing certified bounds')
            iterations.append({'residence':list(previous),'bounds':list(current)})
            answers=updated
            if current==previous:
                break
            previous=current
        else:
            raise RuntimeError('nonincreasing integer refinement did not terminate')
    return {'taskset_proven':True,'status':'CERTIFIED','first_unproven_task_1based':None,
            'initial_bounds':initial,'final_bounds':[x['R'] for x in answers],
            'final_answers':answers,'iterations':iterations}
