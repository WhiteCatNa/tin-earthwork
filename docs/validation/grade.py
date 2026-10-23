"""设计面费用优化仿真：python docs/validation/grade.py [组数] [运输单价] [X 坡度范围 如 -2,2] [Y 坡度范围]"""
import os
import sys
_d = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(_d), os.path.dirname(os.path.dirname(_d))]
import numpy as np
from validate_uncertainty import CORNERS, layout, terrain
from core.calculator import SurveyPoint, TINEarthworkCalculator
from core.grade_opt import CostRates, optimize_grade

groups = int(sys.argv[1]) if len(sys.argv) > 1 else 3
haul = float(sys.argv[2]) if len(sys.argv) > 2 else 1.5
sx = tuple(float(v) for v in sys.argv[3].split(",")) if len(sys.argv) > 3 else (-2.0, 2.0)
sy = tuple(float(v) for v in sys.argv[4].split(",")) if len(sys.argv) > 4 else (-2.0, 2.0)
for seed in range(groups):
    rng = np.random.default_rng(seed)
    f = terrain(rng)
    xy = np.vstack([CORNERS, layout(rng, 400, "random")])
    calc = TINEarthworkCalculator()
    calc.add_points([SurveyPoint(f"P{i}", float(x), float(y), float(f(x, y))) for i, (x, y) in enumerate(xy)])
    calc.set_boundary(CORNERS)
    r = optimize_grade(calc, CostRates(haul=haul), sx, sy)
    flat, fitted = r.baselines
    print(seed, f"best {r.best.cost:,.0f} | flat {flat.cost:,.0f} (saves {1 - r.best.cost / flat.cost:.1%}) | "
                f"least squares {fitted.cost:,.0f} (saves {1 - r.best.cost / fitted.cost:.1%}) | {r.evaluations} evals",
          flush=True)
