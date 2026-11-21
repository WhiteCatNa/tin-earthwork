"""场地放坡：平台 + 自动放坡与棱台解析解对照。"""
import math

import numpy as np
import pytest

from core.calculator import SurveyPoint
from core.slope import Platform, balance_platforms, check_platforms, run_slope, slope_surfaces

SQUARE = [(40.0, 40.0), (60.0, 40.0), (60.0, 60.0), (40.0, 60.0)]


def _ground(func=lambda x, y: 0.0, size=100.0, step=5.0):
    n = int(size / step) + 1
    return [SurveyPoint(f"P{i}-{j}", i * step, j * step, func(i * step, j * step)) for i in range(n) for j in range(n)]


@pytest.mark.parametrize("height,ratio", [(2.1, 1.5), (-2.1, 1.0)])
def test_square_platform_on_flat_ground(height, ratio):
    platform = Platform("P1", SQUARE, height, cut_ratio=ratio, fill_ratio=ratio)
    result = run_slope(_ground(), [platform], spacing=1.0)
    w = abs(height) * ratio                              # 边坡水平宽度
    prism = 400 * abs(height)
    strips = 4 * 20 * abs(height) * w / 2
    corners = math.pi * w * w * abs(height) / 3          # 四个角合成一个圆锥
    expected = prism + strips + corners
    volume, other = (result.total_fill, result.total_cut) if height > 0 else (result.total_cut, result.total_fill)
    assert volume == pytest.approx(expected, rel=2e-3)
    assert other == pytest.approx(0, abs=1e-6)
    platform_part = result.platform_fill if height > 0 else result.platform_cut
    assert platform_part == pytest.approx(prism, rel=1e-9)
    line = result.fill_line if height > 0 else result.cut_line
    assert line
    # 坡脚线（坡顶线）在离平台 w 处：直边中点附近
    mids = np.array([((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in line])
    on_east = mids[(np.abs(mids[:, 1] - 50) < 5)]
    assert np.any(np.abs(on_east[:, 0] - (60 + w)) < 0.05)
    assert not result.warnings


def test_two_platforms_nearest_rule_and_overlap():
    a = Platform("A", [(10, 10), (30, 10), (30, 30), (10, 30)], 1.0)
    b = Platform("B", [(70, 70), (90, 70), (90, 90), (70, 90)], -1.0)
    ds, df = slope_surfaces([a, b], np.array([(20.0, 20.0), (35.0, 20.0), (80.0, 95.0)]))
    assert ds.tolist() == pytest.approx([1.0, 6.0, 4.0])         # 1 + 5/1、−1 + 5/1
    assert df.tolist() == pytest.approx([1.0, 1 - 5 / 1.5, -1 - 5 / 1.5])
    result = run_slope(_ground(), [a, b], spacing=1.0)
    assert result.platform_fill == pytest.approx(400) and result.platform_cut == pytest.approx(400)
    assert result.slope_fill > 0 and result.slope_cut > 0
    with pytest.raises(ValueError, match="重叠"):
        check_platforms([a, Platform("C", [(20, 20), (40, 20), (40, 40)], 0)])
    with pytest.raises(ValueError, match="坡比"):
        check_platforms([Platform("D", SQUARE, 0, cut_ratio=0)])
    with pytest.raises(ValueError, match="添加"):
        check_platforms([])


def test_unclosed_daylight_warns():
    platform = Platform("P", [(0, 0), (100, 0), (100, 30), (0, 30)], 10.0)
    result = run_slope(_ground(), [platform], spacing=2.0)
    assert any("测区边缘" in w for w in result.warnings)


def test_balance_platform_on_sloped_ground():
    ground = _ground(lambda x, y: 0.05 * x)
    platform = Platform("P", SQUARE, 0.0)
    delta, result = balance_platforms(ground, [platform], compact=1.0, spacing=4.0)
    assert result.total_cut == pytest.approx(result.total_fill, rel=1e-2)
    assert 1.5 < delta < 3.5
    round_trip = Platform.from_dict(platform.to_dict())
    assert round_trip == platform
