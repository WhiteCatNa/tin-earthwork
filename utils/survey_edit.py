"""测点列表的增删改与统一坐标平移（不改动土方体积公式）。"""
from typing import List, Optional, Tuple

from core.calculator import SurveyPoint


def add_survey_point(points: List[SurveyPoint], point: SurveyPoint) -> SurveyPoint:
    if not str(point.id).strip():
        raise ValueError("点号不能为空")
    points.append(point)
    return point


def delete_survey_point_at(points: List[SurveyPoint], index: int) -> SurveyPoint:
    if index < 0 or index >= len(points):
        raise IndexError("测点序号超出范围")
    return points.pop(index)


def update_survey_point(
    points: List[SurveyPoint],
    index: int,
    *,
    point_id: Optional[str] = None,
    x: Optional[float] = None,
    y: Optional[float] = None,
    z: Optional[float] = None,
    design_z: Optional[float] = None,
    clear_design_z: bool = False,
) -> SurveyPoint:
    if index < 0 or index >= len(points):
        raise IndexError("测点序号超出范围")
    point = points[index]
    if point_id is not None:
        text = str(point_id).strip()
        if not text:
            raise ValueError("点号不能为空")
        point.id = text
    if x is not None:
        point.x = float(x)
    if y is not None:
        point.y = float(y)
    if z is not None:
        point.z = float(z)
    if clear_design_z:
        point.has_design_z = False
        point.design_z = 0.0
        point.delta_z = 0.0
    elif design_z is not None:
        point.design_z = float(design_z)
        point.has_design_z = True
        point.delta_z = point.z - point.design_z
    elif point.has_design_z:
        point.delta_z = point.z - point.design_z
    return point


def offset_survey_data(
    points: List[SurveyPoint],
    dx: float,
    dy: float,
    dz: float,
    boundary: Optional[List[Tuple[float, float]]] = None,
) -> Optional[List[Tuple[float, float]]]:
    """把测点平移到施工坐标；若有边界则同步平移平面坐标。"""
    for point in points:
        point.x += dx
        point.y += dy
        point.z += dz
        if point.has_design_z:
            point.design_z += dz
            point.delta_z = point.z - point.design_z
    if boundary is None:
        return None
    return [(x + dx, y + dy) for x, y in boundary]


def swap_survey_xy(points: List[SurveyPoint]) -> None:
    """交换每个测点的 X、Y（测量坐标 X 为北向，与 CAD 图纸方向相反时使用）。"""
    for point in points:
        point.x, point.y = point.y, point.x
