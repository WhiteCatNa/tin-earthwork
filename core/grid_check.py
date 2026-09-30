"""
方格网法校核。

在计算范围上铺正方形方格网，角点的地面高程和设计（或后期）高程由三角网线性插值；
每个方格沿左下—右上对角线分成两个三角形（三角棱柱体法），按与 TIN 法相同的方式
精确裁剪、按零线分割挖填。计算范围与 TIN 法一致：计算边界 ∩ 测点覆盖范围。
测区外的角点按最近三角形的平面外推取值，只用来确定方格内的插值，测区外部分不计方量。
方格网只在角点取样，结果与 TIN 法的差异反映了取样密度的影响，用来互相校核。
"""
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from core.calculator import CalculationResult, SurveyPoint, TINEarthworkCalculator
from core.overlay import clip_convex

XY = Tuple[float, float]

# 方格数上限，防止边长填得过小导致长时间计算
MAX_GRID_CELLS = 200_000
# 方格网计入面积低于 TIN 计算面积的该比例时提示
GRID_COVERAGE_WARN_RATIO = 0.995


@dataclass
class GridCorner:
    name: str          # “行-列”，从左下角起算
    row: int
    col: int
    x: float
    y: float
    natural: float     # 地面（前期）高程
    design: float      # 设计（后期）高程
    extrapolated: bool = False   # 位于测点覆盖范围外，按最近三角形平面外推

    @property
    def valid(self) -> bool:
        return math.isfinite(self.natural) and math.isfinite(self.design)

    @property
    def height(self) -> float:
        """施工高度 = 地面 − 设计，正为挖、负为填。"""
        return self.natural - self.design


@dataclass
class GridCell:
    name: str
    row: int
    col: int
    corners: Tuple[str, str, str, str]  # 左下、右下、右上、左上
    area: float = 0.0                   # 计算边界内的面积
    cut: float = 0.0
    fill: float = 0.0
    clipped: bool = False               # 被计算边界裁剪

    @property
    def net(self) -> float:
        return self.cut - self.fill


@dataclass
class GridCheckResult:
    spacing: float
    origin: XY
    rows: int
    cols: int
    corners: List[GridCorner] = field(default_factory=list)
    cells: List[GridCell] = field(default_factory=list)   # 只含面积大于 0 的方格
    total_cut: float = 0.0
    total_fill: float = 0.0
    computed_area: float = 0.0
    skipped_cells: int = 0     # 角点无法取值而未计入的方格
    warnings: List[str] = field(default_factory=list)
    mesh_result: Optional[CalculationResult] = None      # 方格三角形的逐个计算结果

    @property
    def net_volume(self) -> float:
        return self.total_cut - self.total_fill

    def comparison(self, tin: CalculationResult) -> List[Tuple[str, float, float, float, Optional[float]]]:
        """[(项目, TIN 法, 方格网法, 差值, 相对差 %)]；TIN 值接近 0 时相对差为 None。"""
        rows = []
        for label, tin_value, grid_value in (
            ("挖方", tin.total_cut, self.total_cut),
            ("填方", tin.total_fill, self.total_fill),
            ("挖填差值", tin.net_volume, self.net_volume),
            ("计算面积", tin.computed_area, self.computed_area),
        ):
            diff = grid_value - tin_value
            relative = diff / abs(tin_value) * 100 if abs(tin_value) > 1e-6 else None
            rows.append((label, tin_value, grid_value, diff, relative))
        return rows


def suggest_spacing(width: float, height: float) -> float:
    """按场地尺寸建议边长：常用 5/10/20/50 m，方格数控制在约 2500 个以内。"""
    for spacing in (5.0, 10.0, 20.0, 50.0, 100.0):
        if math.ceil(width / spacing) * math.ceil(height / spacing) <= 2500:
            return spacing
    return 200.0


def grid_region(calculator: TINEarthworkCalculator) -> List[XY]:
    """方格网的计算范围：计算边界 ∩ 测点覆盖范围，与 TIN 法实际计算的范围一致。"""
    coverage = calculator.coverage_polygon()
    if not calculator.boundary_polygon:
        return coverage
    boundary = [(float(x), float(y)) for x, y in calculator.boundary_polygon]
    return clip_convex(boundary, coverage)


def run_grid_check(calculator: TINEarthworkCalculator, tin: CalculationResult, spacing: float) -> GridCheckResult:
    """在 TIN 法计算完成后运行方格网校核（calculator 须已构网）。"""
    spacing = float(spacing)
    if not math.isfinite(spacing) or spacing <= 0:
        raise ValueError("方格边长必须是大于 0 的数字")
    region = grid_region(calculator)
    if len(region) < 3:
        raise ValueError("计算边界内没有测点覆盖，无法铺设方格网")
    xmin, ymin = min(x for x, _ in region), min(y for _, y in region)
    xmax, ymax = max(x for x, _ in region), max(y for _, y in region)
    # 方格线对齐到边长的整数倍坐标，便于和图纸对照
    x0 = math.floor(xmin / spacing + 1e-9) * spacing
    y0 = math.floor(ymin / spacing + 1e-9) * spacing
    cols = max(1, math.ceil((xmax - x0) / spacing - 1e-9))
    rows = max(1, math.ceil((ymax - y0) / spacing - 1e-9))
    if rows * cols > MAX_GRID_CELLS:
        raise ValueError(
            f"方格边长 {spacing:g} m 过小：需要 {rows * cols} 个方格，超过 {MAX_GRID_CELLS} 个上限，请加大边长"
        )

    xs = x0 + spacing * np.arange(cols + 1)
    ys = y0 + spacing * np.arange(rows + 1)
    grid_x, grid_y = np.meshgrid(xs, ys)          # [行, 列]
    corner_xy = np.column_stack([grid_x.ravel(), grid_y.ravel()])
    plain_natural, plain_design = calculator.sample_surfaces(corner_xy)
    covered = ~(np.isnan(plain_natural) | np.isnan(plain_design)).reshape(rows + 1, cols + 1)
    natural, design = calculator.sample_surfaces(corner_xy, extrapolate=True)
    natural = natural.reshape(rows + 1, cols + 1)
    design = design.reshape(rows + 1, cols + 1)

    corners: List[GridCorner] = []
    vertex_index = np.full((rows + 1, cols + 1), -1, dtype=np.int64)
    vertex_points: List[SurveyPoint] = []
    for r in range(rows + 1):
        for c in range(cols + 1):
            corner = GridCorner(f"{r + 1}-{c + 1}", r + 1, c + 1, float(xs[c]), float(ys[r]),
                                float(natural[r, c]), float(design[r, c]), extrapolated=not covered[r, c])
            corners.append(corner)
            if corner.valid:
                vertex_index[r, c] = len(vertex_points)
                vertex_points.append(SurveyPoint(
                    corner.name, corner.x, corner.y, corner.natural, design_z=corner.design,
                    delta_z=corner.height, has_design_z=True,
                ))

    ll = vertex_index[:-1, :-1]
    lr = vertex_index[:-1, 1:]
    ur = vertex_index[1:, 1:]
    ul = vertex_index[1:, :-1]
    complete = (ll >= 0) & (lr >= 0) & (ur >= 0) & (ul >= 0)
    cell_rows, cell_cols = np.nonzero(complete)
    if not len(cell_rows):
        raise ValueError("方格网内没有四个角点都在测区内的方格，请减小边长或检查计算范围")
    simplices = np.concatenate([
        np.column_stack([ll[complete], lr[complete], ur[complete]]),
        np.column_stack([ll[complete], ur[complete], ul[complete]]),
    ])

    mesh = TINEarthworkCalculator()
    mesh.set_boundary(region)
    mesh.build_from_mesh(vertex_points, simplices)
    mesh_result = mesh.calculate_volumes()

    count = len(cell_rows)
    cells: List[GridCell] = []
    for k, (r, c) in enumerate(zip(cell_rows.tolist(), cell_cols.tolist())):
        halves = (mesh_result.triangles[k], mesh_result.triangles[k + count])
        area = sum(t.area for t in halves if not t.is_boundary)
        if area <= 0:
            continue
        cells.append(GridCell(
            name=f"{r + 1}-{c + 1}", row=r + 1, col=c + 1,
            corners=(f"{r + 1}-{c + 1}", f"{r + 1}-{c + 2}", f"{r + 2}-{c + 2}", f"{r + 2}-{c + 1}"),
            area=area,
            cut=sum(t.cut_volume for t in halves if not t.is_boundary),
            fill=sum(t.fill_volume for t in halves if not t.is_boundary),
            clipped=any(t.is_clipped or t.is_boundary for t in halves),
        ))

    result = GridCheckResult(
        spacing=spacing, origin=(float(x0), float(y0)), rows=rows, cols=cols,
        corners=corners, cells=cells,
        total_cut=mesh_result.total_cut, total_fill=mesh_result.total_fill,
        computed_area=mesh_result.computed_area,
        skipped_cells=int((~complete).sum()), mesh_result=mesh_result,
    )
    if tin.computed_area > 0 and result.computed_area < tin.computed_area * GRID_COVERAGE_WARN_RATIO:
        missing = tin.computed_area - result.computed_area
        result.warnings.append(
            f"方格网少计 {missing:.1f} m²（{missing / tin.computed_area * 100:.1f}%）："
            "有方格的角点无法取得高程，整格未计入。"
        )
    return result
