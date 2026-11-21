import sys; import os; _d=os.path.dirname(os.path.abspath(__file__)); sys.path[:0]=[os.path.dirname(_d), os.path.dirname(os.path.dirname(_d))]
import numpy as np
from validate_uncertainty import terrain, layout, CORNERS, SIZE
from core.calculator import SurveyPoint, TINEarthworkCalculator
from core.uncertainty import evaluate_uncertainty
def smooth_box(x,y,x0,x1,y0,y1,w=8):
    s=lambda t: 1/(1+np.exp(-t/w*4))
    return s(x-x0)*s(x1-x)*s(y-y0)*s(y1-y)
n=int(sys.argv[1]); kind=sys.argv[2]; groups=int(sys.argv[3]); sm=0.02
R=[];Z=[]
for seed in range(groups):
    rng=np.random.default_rng(3000+seed); f=terrain(rng)
    cx,cy=rng.uniform(40,120,2); mx,my=rng.uniform(40,120,2)
    change=lambda x,y: -1.0*smooth_box(x,y,cx,cx+40,cy,cy+40)+0.6*smooth_box(x,y,mx,mx+30,my,my+30)
    g=lambda x,y: f(x,y)+change(x,y)
    pts=[]
    for fun,pre in ((f,"A"),(g,"B")):
        xy=np.vstack([CORNERS,layout(rng,n,kind)]); z=fun(xy[:,0],xy[:,1])+rng.normal(0,sm,len(xy))
        pts.append([SurveyPoint(f"{pre}{i}",float(x),float(y),float(v)) for i,((x,y),v) in enumerate(zip(xy,z))])
    calc=TINEarthworkCalculator(); calc.add_points(pts[0]); calc.set_compare_points(pts[1]); calc.set_boundary(CORNERS)
    res=calc.run_full_calculation(); u=evaluate_uncertainty(calc,res,sm,seed=seed); s=u.significance
    ny,nx=s.dz.shape; gx=s.origin[0]+(np.arange(nx)+.5)*s.cell; gy=s.origin[1]+(np.arange(ny)+.5)*s.cell; X,Y=np.meshgrid(gx,gy)
    true=-change(X,Y)  # before - after
    valid=~np.isnan(s.dz)
    still=(np.abs(true)<0.01)&valid; moved=(np.abs(true)>0.2)&valid
    sig=(s.classes!=0)&valid; fix=(np.abs(np.nan_to_num(s.dz))>s.fixed_lod)&valid
    a=s.cell**2
    tc=np.clip(true,0,None)[valid].sum()*a; tf=-np.clip(true,None,0)[valid].sum()*a
    zs=(np.nan_to_num(s.dz)/np.where(valid,s.lod/1.959964,1))[still]; Z.append(zs)
    R.append((sig[still].mean(),fix[still].mean(),sig[moved].mean(),fix[moved].mean(), tc, tf, res.total_cut, res.total_fill, s.significant_cut, s.significant_fill))
R=np.array(R)
zz=np.concatenate(Z); print("z rms still",np.sqrt((zz**2).mean()).round(2),"q95 |z|",np.quantile(abs(zz),0.95).round(2))
print(n,kind,groups,"false+ ours %.3f fixed %.3f | detect ours %.3f fixed %.3f | true cut/fill %.0f %.0f | TIN %.0f %.0f | significant %.0f %.0f"%tuple(R.mean(0)))
