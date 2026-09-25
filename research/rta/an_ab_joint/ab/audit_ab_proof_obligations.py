"""Independent finite checks of workload and real-energy proof interfaces."""
from collections import deque
from fractions import Fraction
from itertools import product
import json


def workload_audit():
    cases=states_seen=checks=0
    for c in range(1,4):
        for period in range(c,6):
            for rho in range(c,period+1):
                cases+=1;root=(0,0,0,(0,)*6);seen={root};queue=deque([root])
                def bound(length):
                    x=length+rho-c
                    return (x//period)*c+min(c,x%period)
                while queue:
                    cd,rem,age,bits=queue.popleft();states_seen+=1
                    for demand in range(c+1) if cd==0 and not rem else (0,):
                        dc,rr,aa=(period,demand,0) if demand else (cd,rem,age)
                        if rr:
                            for length in range(1,7):
                                # A virtual ready execution, before first violation.
                                actual=sum(bits[-(length-1):]) if length>1 else 0
                                assert actual+1<=bound(length),(c,period,rho,length,bits,aa)
                                checks+=1
                        for execute in (0,1) if rr else (0,):
                            left=rr-execute;new_age=aa+1 if left else 0
                            new_bits=bits[1:]+(execute,)
                            for length in range(1,7):
                                assert sum(new_bits[-length:])<=bound(length)
                                checks+=1
                            if left and new_age>=rho:continue
                            state=(max(0,dc-1),left,new_age,new_bits)
                            if state not in seen:seen.add(state);queue.append(state)
    return dict(cases=cases,closed_states=states_seen,inequality_checks=checks,status='PASS')


def quantization_audit():
    checks=traces=0;powers=(1,2)
    for e0 in (Fraction(x,4) for x in range(13)):
        for hs in product((Fraction(0),Fraction(1,2),Fraction(1),Fraction(3,2)),repeat=4):
            e=e0;integer=int(e0);total=e0;prefix=[e0];traces+=1
            for h in hs:
                def chosen(energy):
                    return max(range(3),key=lambda n:n if sum(powers[:n])<=energy else -1)
                a,b=chosen(e),chosen(integer);assert a==b
                integer_h=int(total+h)-int(total)
                e+=h-sum(powers[:a]);integer+=integer_h-sum(powers[:b]);total+=h;prefix.append(total)
                assert integer==int(e)
                for earlier in prefix[:-1]:
                    assert int(total)-int(earlier)>=int(total-earlier);checks+=1
    return dict(traces=traces,interval_checks=checks,status='PASS')


if __name__=='__main__':print(json.dumps(dict(workload=workload_audit(),quantization=quantization_audit()),indent=2))
