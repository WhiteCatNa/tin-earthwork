import sys; import os; _d=os.path.dirname(os.path.abspath(__file__)); sys.path[:0]=[os.path.dirname(_d), os.path.dirname(os.path.dirname(_d))]
import numpy as np
from validate_uncertainty import *
from core.survey_plan import plan_survey
n,kind,groups,tau=int(sys.argv[1]),sys.argv[2],int(sys.argv[3]),float(sys.argv[4]); sm=0.02; base=int(sys.argv[5]) if len(sys.argv)>5 else 7000
res_all=[]
for seed in range(groups):
    rng0=np.random.default_rng(base+seed); f=terrain(rng0)
    xy0=np.vstack([CORNERS,layout(rng0,n,kind)]); z0=f(xy0[:,0],xy0[:,1])+rng0.normal(0,sm,len(xy0)); T=true_volumes(f)
    out=[]
    for strategy in ("rec","rand"):
        rng=np.random.default_rng(99+seed); xy,z=xy0.copy(),z0.copy(); rounds=0; first=None
        while True:
            calc,res,tin=volumes(xy,z); u=evaluate_uncertainty(calc,res,sm,recommend=min(1000,len(xy)),seed=seed); p=plan_survey(u,len(xy),tau)
            if first is None: first=p
            if p.reached or rounds>=8 or p.conservative is None: break
            k=len(p.batch)
            add=np.array([(r.x,r.y) for r in p.batch]) if strategy=="rec" else rng.uniform(0,SIZE,(k,2))
            xy=np.vstack([xy,add]); z=np.r_[z,f(add[:,0],add[:,1])+rng.normal(0,sm,k)]; rounds+=1
        err=np.abs(np.array(u.estimate)-T)[:2]/T[:2]; tinerr=np.abs(tin-T)[:2]/T[:2]
        out.append((len(xy)-len(xy0),rounds,p.reached,err.max(),tinerr.max()))
    res_all.append((first.current,first.optimistic,first.conservative,out))
    print(seed,f"start ±{first.current:.1%} pred {first.optimistic}~{first.conservative}","rec",out[0],"rand",out[1],flush=True)
need=[r for r in res_all if r[0]>tau]
A=np.array([[r[3][0][0],r[3][1][0]] for r in need]); 
print("needing infill:",len(need),"of",len(res_all))
print("added mean rec/rand",A.mean(0).round(0),"median",np.median(A,0))
print("reached rec/rand",np.mean([r[3][0][2] for r in need]),np.mean([r[3][1][2] for r in need]))
print("true corrected err<=tau rec/rand",np.mean([r[3][0][3]<=tau for r in need]),np.mean([r[3][1][3]<=tau for r in need]))
print("rec added within pred range",np.mean([(r[1] or 0)<=r[3][0][0]<=(r[2] or 1e9) for r in need]), "<= conservative", np.mean([r[3][0][0]<=(r[2] or 1e9) for r in need]))
