"""ASAP-BLOCK joint RTA with the H/T augmented-progress phase split.

Research implementation for proof/validation.  The processor term, workload bound,
blocking-sequence search, and joint least-fixed-point construction are inherited
unchanged from ab_joint.  Only the PH stage envelope is tightened by separating
whether the q-th augmented progress is a full-HP tick (H) or a target-execution
tick (T).
"""
from __future__ import annotations
from dataclasses import replace
from functools import cache
from fractions import Fraction
from typing import Sequence, Mapping

import ab_joint
import asap_block_rta_v9_3_ph as ph
import asap_block_rta_v9_3 as core

VERSION = "AB_SEQ_ALL_TASK_LEAST_JOINT_HT_V2"
MODEL = ab_joint.MODEL + "+HT_PHASE_SPLIT"


def _validate_t_witness(witness, *, target, hp_tasks, lp_tasks, q, h, processors,
                        theta_by_name, nodes, edges):
    """Independent structural validation against the tightened T network."""
    ph._validate_point(target, hp_tasks, lp_tasks, target.deadline, q, h,
                       processors, theta_by_name)
    z = witness.target_exec_z
    if type(z) is not int or not 1 <= z <= min(target.wcet, q):
        return False
    hist_z = z - 1
    scale = ph._power_scale((target,) + tuple(hp_tasks) + tuple(lp_tasks))
    if type(witness.integer_cost_scale) is not int or witness.integer_cost_scale != scale:
        return False
    if type(witness.scaled_variable_cost) is not int or not isinstance(witness.energy, Fraction):
        return False
    # Reconstruct the T network from mathematical inputs rather than trusting
    # the caller's edge capacities or the witness's declared scale.
    expected_nodes, original_edges = ph._build_branch(
        target, hp_tasks, lp_tasks, q, h, processors, theta_by_name, z, scale)
    expected_edges = []
    for edge in original_edges:
        if edge.key.startswith('source->lp:') or edge.key.startswith('lp->agg:'):
            expected_edges.append(replace(edge, upper=min(edge.upper, hist_z)))
        elif edge.key == 'agg->S':
            expected_edges.append(replace(edge, upper=(processors-1)*hist_z))
        else:
            expected_edges.append(edge)
    if tuple(nodes) != expected_nodes or tuple(edges) != tuple(expected_edges):
        return False
    if len(witness.edge_flows) != len(edges):
        return False
    for observed, expected in zip(witness.edge_flows, edges):
        if (observed.key, observed.source, observed.sink, observed.lower,
            observed.upper, observed.cost) != (expected.key, expected.source,
                                                expected.sink, expected.lower,
                                                expected.upper, expected.cost):
            return False
        if type(observed.flow) is not int or not observed.lower <= observed.flow <= observed.upper:
            return False
    for node in nodes:
        incoming = sum(e.flow for e in witness.edge_flows if e.sink == node)
        outgoing = sum(e.flow for e in witness.edge_flows if e.source == node)
        if incoming != outgoing:
            return False
    flows = {edge.key: edge.flow for edge in witness.edge_flows}
    if tuple(a.task_name for a in witness.hp_allocations) != tuple(t.name for t in hp_tasks):
        return False
    if tuple(a.task_name for a in witness.lp_allocations) != tuple(t.name for t in lp_tasks):
        return False
    if sum(a.g for a in witness.hp_allocations) != processors * (q-z):
        return False
    if sum(a.e for a in witness.hp_allocations) > (processors-1)*h:
        return False
    for task,a in zip(hp_tasks,witness.hp_allocations):
        if any(type(v) is not int or v < 0 for v in (a.g,a.s,a.e)):
            return False
        if a.g > q-z or a.s > z or a.e > h:
            return False
        if a.g+a.s+a.e > core.workload_bound_v9_3(task,q+h,theta_by_name[task.name]):
            return False
        if (a.g != flows['hp->G:' + task.name]
                or a.s != flows['hp->S:' + task.name]
                or a.e != flows['hp->E:' + task.name]
                or a.g+a.s+a.e != flows['source->hp:' + task.name]):
            return False
    prev = q+h-1
    for task,a in zip(lp_tasks,witness.lp_allocations):
        upper = min(core.deadline_workload_bound_v9_3(task,prev), prev, hist_z)
        if type(a.ell) is not int or not 0 <= a.ell <= upper:
            return False
        if (a.ell != flows['lp->agg:' + task.name]
                or a.ell != flows['source->lp:' + task.name]):
            return False
    lp_sum = sum(a.ell for a in witness.lp_allocations)
    if lp_sum > (processors-1)*hist_z:
        return False
    if sum(a.s for a in witness.hp_allocations)+lp_sum > (processors-1)*z:
        return False
    expected = z*target.power
    expected += sum((a.g+a.s+a.e)*t.power for t,a in zip(hp_tasks,witness.hp_allocations))
    expected += sum(a.ell*t.power for t,a in zip(lp_tasks,witness.lp_allocations))
    scaled_cost = sum(edge.cost * edge.flow for edge in witness.edge_flows)
    if scaled_cost != witness.scaled_variable_cost:
        return False
    if Fraction(-scaled_cost, scale) + z*target.power != expected:
        return False
    return witness.energy == expected


def split_phase_energy(*, target, hp_tasks, lp_tasks, q, h, processors,
                       theta_by_name):
    """Exact maximum energy over the H union T split phase family.

    Returns None iff both branch families are infeasible.
    """
    # Use the frozen point validator. w is irrelevant to the stage combinatorics
    # except as an input-domain guard; the production AB engine passes D_k.
    w = target.deadline
    ph._validate_point(target,hp_tasks,lp_tasks,w,q,h,processors,theta_by_name)
    scale = ph._power_scale((target,)+tuple(hp_tasks)+tuple(lp_tasks))
    deadline = ph._Deadline(None, ph.time.monotonic)
    optima = []

    # H branch: current augmented progress is a full-HP tick.  All z target
    # executions are historical, hence an unfinished target implies z <= C_k-1;
    # also only q-1 progress ticks are historical, hence z <= q-1.
    if target.wcet >= 1 and q >= 1:
        for z in range(0, min(target.wcet-1, q-1)+1):
            nodes,edges = ph._build_branch(target,hp_tasks,lp_tasks,q,h,processors,
                                           theta_by_name,z,scale)
            result = ph._solve_min_cost_circulation(nodes,edges,deadline)
            if result.status is ph._FlowStatus.INFEASIBLE:
                continue
            if result.status is not ph._FlowStatus.OPTIMAL:
                raise RuntimeError(f'H branch flow failure: {result.status} {result.reason}')
            wit = ph._witness_from_flow(target,hp_tasks,lp_tasks,z,scale,edges,result)
            if not ph.validate_phase_witness_v9_3(
                wit,target=target,hp_tasks=hp_tasks,lp_tasks=lp_tasks,w=w,q=q,h=h,
                processors=processors,theta_by_name=theta_by_name,_deadline=deadline):
                raise RuntimeError('H branch witness validation failed')
            optima.append((wit.energy,'H',z))

    # T branch: current augmented progress executes the target.  Exactly one of
    # the z target-execution ticks is the current hypothetical tick, so LP
    # history can occupy only z-1 historical target-execution ticks.
    for z in range(1, min(target.wcet,q)+1):
        nodes,old_edges = ph._build_branch(target,hp_tasks,lp_tasks,q,h,processors,
                                           theta_by_name,z,scale)
        hist_z = z-1
        tightened=[]
        for edge in old_edges:
            if edge.key.startswith('source->lp:') or edge.key.startswith('lp->agg:'):
                tightened.append(replace(edge,upper=min(edge.upper,hist_z)))
            elif edge.key == 'agg->S':
                tightened.append(replace(edge,upper=(processors-1)*hist_z))
            else:
                tightened.append(edge)
        edges=tuple(tightened)
        result = ph._solve_min_cost_circulation(nodes,edges,deadline)
        if result.status is ph._FlowStatus.INFEASIBLE:
            continue
        if result.status is not ph._FlowStatus.OPTIMAL:
            raise RuntimeError(f'T branch flow failure: {result.status} {result.reason}')
        wit=ph._witness_from_flow(target,hp_tasks,lp_tasks,z,scale,edges,result)
        if not _validate_t_witness(wit,target=target,hp_tasks=hp_tasks,lp_tasks=lp_tasks,
                                   q=q,h=h,processors=processors,
                                   theta_by_name=theta_by_name,nodes=nodes,edges=edges):
            raise RuntimeError('T branch witness validation failed')
        optima.append((wit.energy,'T',z))

    if not optima:
        return None
    return max(e for e,_b,_z in optima)


class HTEngine(ab_joint.Engine):
    def __init__(self,tasks,m,beta,e0,rho):
        super().__init__(tasks,m,beta,e0,rho)
        self.phase = cache(self._ht_phase)

    def _ht_phase(self,k,q,h):
        energy=split_phase_energy(target=self.tasks[k],hp_tasks=self.tasks[:k],
                                  lp_tasks=self.lp_views[k+1:],q=q,h=h,
                                  processors=self.m,theta_by_name=self.theta)
        if energy is None:
            return None
        if not isinstance(energy,Fraction) or energy.denominator != 1:
            raise RuntimeError('noninteger exact HT phase result')
        return int(energy)


def evaluate(tasks,m,beta,e0,rho):
    engine=HTEngine(tasks,m,beta,e0,rho)
    try:
        return [engine.candidate(k) for k in range(len(tasks))]
    finally:
        engine.clear()


def verify_certificate(tasks,m,beta,e0,cert):
    tasks,beta=ab_joint.normalize(tasks,m,beta,e0)
    ab_joint.require(isinstance(cert,dict) and cert.get('version')==VERSION,'certificate version')
    ab_joint.require(cert.get('model') == MODEL, 'certificate model')
    ab_joint.require(cert.get('input_sha256') == ab_joint.fingerprint(tasks,m,beta,e0),
                     'certificate/input mismatch')
    rho,responses,details=(cert.get(k) for k in ('residence_bounds','response_bounds','details'))
    ab_joint.require(all(isinstance(v,list) and len(v)==len(tasks) for v in (rho,responses,details)),
                     'incomplete certificate')
    ab_joint.require(all(ab_joint.integer(w,t.wcet) and ab_joint.integer(r,w) and r<=t.deadline
                         for t,w,r in zip(tasks,responses,rho)),'not a joint post-fixed certificate')
    engine=HTEngine(tasks,m,beta,e0,rho); checks=0
    try:
        for k,(w,detail) in enumerate(zip(responses,details)):
            ab_joint.require(isinstance(detail,dict) and detail.get('R')==w,'candidate mismatch')
            a=engine.progress(k,w)
            ab_joint.require(a<=w and detail.get('A')==a,'incorrect processor progress')
            hs,points=detail.get('h'),detail.get('checkpoints')
            ab_joint.require(isinstance(hs,list) and isinstance(points,list) and len(hs)==len(points)==a,
                             'missing progress checkpoint')
            previous=0
            for q,(h,pt) in enumerate(zip(hs,points),1):
                ab_joint.require(ab_joint.integer(h,previous) and h<=w-a,'invalid blocking sequence')
                g=engine.phase(k,q,h); supply=e0+beta[q+h-1]
                ab_joint.require(g is None or g<=supply,'unsafe energy checkpoint')
                ab_joint.require(pt=={'q':q,'h':h,'energy':g,'supply':supply},'checkpoint changed')
                previous=h; checks+=1
    finally:
        engine.clear()
    return {'tasks':len(tasks),'checkpoints':checks,'status':'PASS'}


def certificate(tasks,m,beta,e0,rho,answers):
    cert={'version':VERSION,'model':MODEL,
          'input_sha256':ab_joint.fingerprint(tasks,m,beta,e0),'residence_bounds':list(rho),
          'response_bounds':[a['R'] for a in answers],'details':answers}
    check=verify_certificate(tasks,m,beta,e0,cert)
    return cert,check


def least_certificate(tasks,m,beta,e0,max_iterations=None):
    tasks,beta=ab_joint.normalize(tasks,m,beta,e0)
    natural=1+sum(t.deadline-t.wcet for t in tasks)
    if max_iterations is not None:
        ab_joint.require(ab_joint.integer(max_iterations,1), 'invalid iteration limit')
    limit=natural if max_iterations is None else min(natural,max_iterations)
    rho=tuple(t.wcet for t in tasks); history=[]
    for iteration in range(limit):
        answers=evaluate(tasks,m,beta,e0,rho)
        vector=tuple(a['R'] if a else None for a in answers)
        history.append({'rho':list(rho),'outputs':list(vector)})
        if any(v is None for v in vector):
            return {'version':VERSION,'status':'NO_CERTIFICATE_IN_THIS_FAMILY',
                    'taskset_proven':False,'certificate':None,'history':history}
        ab_joint.require(all(r<=w for r,w in zip(rho,vector)),'ascending monotonicity violated')
        if vector==rho:
            cert,check=certificate(tasks,m,beta,e0,rho,answers)
            return {'version':VERSION,'status':'CERTIFIED','taskset_proven':True,
                    'certificate':cert,'verification':check,'history':history}
        rho=vector
    ab_joint.require(limit < natural, 'finite integer search did not terminate')
    return {'version':VERSION,'status':'ITERATION_LIMIT','taskset_proven':False,
            'certificate':None,'history':history}
