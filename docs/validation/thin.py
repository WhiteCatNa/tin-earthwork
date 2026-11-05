import sys,time; import os; _d=os.path.dirname(os.path.abspath(__file__)); sys.path[:0]=[os.path.dirname(_d), os.path.dirname(os.path.dirname(_d))]
import numpy as np
from validate_uncertainty import terrain, true_volumes, CORNERS, SIZE
from utils.terrain_import import adaptive_thin, thin_cloud, THIN_CENTER, THIN_LOWEST
from core.calculator import SurveyPoint, TINEarthworkCalculator
def vol(xyz):
    c=TINEarthworkCalculator(50.0); c.add_points([SurveyPoint(str(i),*p) for i,p in enumerate(xyz.tolist())]); c.set_boundary(CORNERS)
    r=c.run_full_calculation(); return np.array([r.total_cut,r.total_fill])
N=int(sys.argv[1]); noise=float(sys.argv[2]); tols=[float(t) for t in sys.argv[3].split(',')]
for seed in range(int(sys.argv[4])):
    rng=np.random.default_rng(seed); f=terrain(rng)
    xy=np.vstack([CORNERS,rng.uniform(0,SIZE,(N,2))]); xyz=np.column_stack([xy,f(xy[:,0],xy[:,1])+rng.normal(0,noise,len(xy))])
    T=true_volumes(f); t=time.time(); Vfull=vol(xyz); 
    for tol in tols:
        t=time.time(); k=adaptive_thin(xyz,tol); ta=time.time()-t
        Va=vol(xyz[k]); m=len(k)
        cell=SIZE/np.sqrt(m); g=thin_cloud(xyz,cell,THIN_CENTER); Vg=vol(xyz[g])
        print(seed,f"tol {tol}: keep {m} ({m/len(xyz):.2%}) {ta:.1f}s | vs full: adaptive {np.abs(Va-Vfull).round(0)} grid({len(g)}) {np.abs(Vg-Vfull).round(0)} | vs true: full {np.abs(Vfull-T[:2]).round(0)} adaptive {np.abs(Va-T[:2]).round(0)} grid {np.abs(Vg-T[:2]).round(0)}",flush=True)
