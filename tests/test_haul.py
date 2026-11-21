"""土方调配：守恒、重心精确值、最优方案与手算对照。"""
import math

import numpy as np
import pytest

from core.calculator import SurveyPoint, TINEarthworkCalculator
from core.haul import (BORROW, WASTE, check_balance, export_haul_excel, haul_rows, moves_between_zones,
                       run_haul, suggest_zone_size)


def _calc(func, size=40.0, step=2.0, design=0.0, boundary=None):
    calc = TINEarthworkCalculator(design)
    n = int(size / step) + 1
    calc.add_points([SurveyPoint(f"P{i}-{j}", i * step, j * step, func(i * step, j * step))
                     for i in range(n) for j in range(n)])
    if boundary:
        calc.set_boundary(boundary)
    return calc, calc.run_full_calculation()


def test_two_zones_exact_centroids_and_distance():
    # 地面 z = 1 − x/20（x 0~40），设计 0：x<20 挖、x>20 填，各 20×40×1/2 = 400
    calc, result = _calc(lambda x, y: 1 - x / 20)
    haul = run_haul(calc, 20.0)
    names = sorted(z.name for z in haul.zones)
    assert names == ["A1", "A2", "B1", "B2"]
    a1 = haul.zone("A1")
    assert a1.cut == pytest.approx(200) and a1.fill == pytest.approx(0, abs=1e-9)
    # 三角形楔体重心：距高边 1/3 宽度 → x = 20/3；y 为 0~20 的中点
    assert a1.cut_center == pytest.approx((20 / 3, 10))
    assert haul.zone("B1").fill_center == pytest.approx((40 - 20 / 3, 10))
    assert haul.waste == pytest.approx(0, abs=1e-6) and haul.borrow == pytest.approx(0, abs=1e-6)
    # 最优：A1→B1、A2→B2，运距 40 − 40/3
    assert haul.work == pytest.approx(400 * (40 - 40 / 3), rel=1e-6)
    assert haul.average_distance == pytest.approx(40 - 40 / 3)
    assert max(check_balance(haul).values()) < 1e-6
    assert sum(z.cut for z in haul.zones) == pytest.approx(result.total_cut)


def test_compaction_waste_and_local_balance():
    calc, result = _calc(lambda x, y: 1 - x / 20)
    haul = run_haul(calc, 40.0, compact=0.8)        # 单一调配区：区内平衡，余土外运
    assert len(haul.zones) == 1 and haul.hauled == 0
    zone = haul.zones[0]
    assert zone.local == pytest.approx(400) and zone.supply == pytest.approx(0, abs=1e-9)
    assert zone.demand == pytest.approx(100)        # 填方需 400/0.8 = 500 自然方
    assert haul.borrow == pytest.approx(100) and any(m.source == BORROW for m in haul.moves)

    haul = run_haul(calc, 20.0, compact=1.25)       # 填方只需 320 → 余土 80 外运
    assert haul.waste == pytest.approx(80) and any(m.target == WASTE for m in haul.moves)
    assert max(check_balance(haul).values()) < 1e-6


def test_optimal_against_greedy_and_brute_force():
    rng = np.random.default_rng(3)
    coeff = rng.normal(size=6)

    def ground(x, y):
        return (coeff[0] * math.sin(x / 9) + coeff[1] * math.cos(y / 7) + coeff[2] * math.sin((x + y) / 11)) * 0.5

    calc, result = _calc(ground, size=60, step=3)
    haul = run_haul(calc, 15.0)
    assert sum(z.cut for z in haul.zones) == pytest.approx(result.total_cut, rel=1e-9)
    assert sum(z.fill for z in haul.zones) == pytest.approx(result.total_fill, rel=1e-9)
    assert max(check_balance(haul).values()) < 1e-5
    # 打乱方案（把两条调运互换目标）不会更省
    inner = [m for m in haul.moves if m.target != WASTE and m.source != BORROW]
    if len(inner) >= 2:
        a, b = inner[0], inner[1]
        moved = min(a.volume, b.volume)
        za, zb = haul.zone(a.source), haul.zone(b.source)
        ta, tb = haul.zone(a.target), haul.zone(b.target)
        delta = moved * (math.dist(za.cut_center, tb.fill_center) + math.dist(zb.cut_center, ta.fill_center)
                         - a.distance - b.distance)
        assert delta >= -1e-6
    assert all(len(arrow) == 3 for arrow in moves_between_zones(haul))


def test_hole_boundary_and_export(tmp_path):
    boundary = [(0, 0), (40, 0), (40, 40), (0, 40)]
    calc = TINEarthworkCalculator(0.0)
    calc.add_points([SurveyPoint(f"P{i}-{j}", i * 2.0, j * 2.0, 1 - i * 2.0 / 20) for i in range(21) for j in range(21)])
    calc.set_boundary(boundary)
    calc.set_holes([[(5, 5), (15, 5), (15, 15), (5, 15)]])
    result = calc.run_full_calculation()
    haul = run_haul(calc, 20.0)
    assert sum(z.cut for z in haul.zones) == pytest.approx(result.total_cut, rel=1e-9)
    zones, moves = haul_rows(haul)
    assert zones and moves
    path = tmp_path / "haul.xlsx"
    export_haul_excel(haul, str(path), "测试")
    from openpyxl import load_workbook
    assert load_workbook(path).active.title == "土方调配"
    assert suggest_zone_size(calc) in (5.0, 10.0)
    with pytest.raises(ValueError, match="过小"):
        run_haul(calc, 0.5)
    with pytest.raises(ValueError):
        run_haul(calc, -1)
