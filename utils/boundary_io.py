"""现场采集的边界：读取带点号的边界点文件，以及把一串点号解析成边界顶点。"""
import fnmatch
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from core.calculator import SurveyPoint
from utils.data_handler import DataValidator
from utils.dxf_io import read_text_with_encodings

XY = Tuple[float, float]

RANGE_SEPARATORS = "-~～—–"
MAX_RANGE_POINTS = 10000


@dataclass
class BoundaryPoints:
    """从文件读到的边界点，按文件里的顺序。"""
    points: List[XY] = field(default_factory=list)
    ids: List[str] = field(default_factory=list)       # 与 points 一一对应；文件里没有点号时为空串
    layout: str = ""                                   # 各列是怎么认的，给用户核对
    skipped: List[str] = field(default_factory=list)   # 无法解析而跳过的行


def _number(cell) -> Optional[float]:
    if isinstance(cell, bool) or cell is None:
        return None
    try:
        value = float(cell)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _text(cell) -> str:
    """单元格的文字；Excel 里的整数值（12.0）写成 12。"""
    if cell is None:
        return ""
    if isinstance(cell, float):
        if math.isnan(cell):
            return ""
        if cell.is_integer():
            return str(int(cell))
    return str(cell).strip()


def _is_whole(cell) -> bool:
    """像点号的数字：正整数，且不是写成 12.000 这样带小数位的坐标。"""
    value = _number(cell)
    if value is None or value < 1 or not float(value).is_integer():
        return False
    return not isinstance(cell, str) or not re.search(r"[.eE]", cell)


def _split_fields(line: str) -> List[str]:
    for separator in (",", "，", ";", "；", "\t"):
        if separator in line:
            return [part.strip() for part in re.split(r"[,，;；\t]", line)]
    return line.split()


def _read_rows(filepath: str) -> List[List[object]]:
    """把文件读成一行行的单元格，不假定有表头。"""
    suffix = Path(filepath).suffix.lower()
    if suffix in (".xlsx", ".xls"):
        frame = pd.read_excel(filepath, header=None, dtype=object)
        rows = [[None if pd.isna(cell) else cell for cell in row] for row in frame.itertuples(index=False)]
    else:
        rows = []
        for raw in read_text_with_encodings(filepath).splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", "*", "//")) or line.upper() in {"BEGIN", "END"}:
                continue
            rows.append(_split_fields(line))
    return [row for row in rows if any(_text(cell) for cell in row)]


def _numeric_count(row: Sequence[object]) -> int:
    return sum(_number(cell) is not None for cell in row)


def _looks_like_cass(row: Sequence[object]) -> bool:
    """CASS .dat：点号,编码,东,北,高 —— 第 2 个字段是编码（常为空），后三个是数字。"""
    return (
        len(row) >= 5
        and _number(row[1]) is None
        and all(_number(cell) is not None for cell in row[2:5])
    )


def _columns_from_header(header: Sequence[object]) -> Optional[Tuple[Optional[int], int, int]]:
    """按表头文字认列，规则与测点导入相同；X、Y 认不全时返回 None。"""
    names = [_text(cell) for cell in header]
    mapping = DataValidator.auto_detect_columns(pd.DataFrame(columns=[name for name in names if name]))
    if "x" not in mapping or "y" not in mapping:
        return None
    id_column = names.index(mapping["id"]) if "id" in mapping else None
    return id_column, names.index(mapping["x"]), names.index(mapping["y"])


def _columns_by_position(rows: Sequence[Sequence[object]]) -> Tuple[Optional[int], int, int]:
    """没有可用表头时，按各列的内容判断：点号、X、Y，还是 X、Y。"""
    width = Counter(len(row) for row in rows).most_common(1)[0][0]
    sample = [row for row in rows if len(row) >= width]

    def column(index: int) -> List[object]:
        return [row[index] for row in sample]

    def mostly_numeric(index: int) -> bool:
        cells = column(index)
        return sum(_number(cell) is not None for cell in cells) >= 0.9 * len(cells)

    if width >= 3 and not mostly_numeric(0):
        numeric = [index for index in range(1, width) if mostly_numeric(index)]
        if len(numeric) < 2:
            raise ValueError("没有找到 X、Y 两列数字")
        return 0, numeric[0], numeric[1]
    if width >= 3:
        first = column(0)
        whole_ids = all(_is_whole(cell) for cell in first) and len({_text(cell) for cell in first}) == len(first)
        others_have_decimals = any(not _is_whole(cell) for index in (1, 2) for cell in column(index))
        if whole_ids and (width >= 4 or others_have_decimals):
            return 0, 1, 2
    if width < 2:
        raise ValueError("文件列数不足，至少需要 X、Y 两列")
    return None, 0, 1


def _tidy(points: List[XY], ids: List[str]) -> Tuple[List[XY], List[str]]:
    """去掉连续重复的点，以及为了闭合而重复测的首点。"""
    kept_points: List[XY] = []
    kept_ids: List[str] = []
    for point, point_id in zip(points, ids):
        if kept_points and kept_points[-1] == point:
            continue
        kept_points.append(point)
        kept_ids.append(point_id)
    if len(kept_points) >= 2 and kept_points[0] == kept_points[-1]:
        kept_points.pop()
        kept_ids.pop()
    return kept_points, kept_ids


def read_boundary_points(filepath: str) -> BoundaryPoints:
    """读取现场采集的边界点文件（Excel / CSV / TXT / CASS .dat），按文件顺序连成边界。

    带表头时按列名认 X、Y（规则与测点导入相同）；没有表头时按内容判断是
    “点号、X、Y(、高程)”还是“X、Y(、高程)”。CASS .dat 的东坐标作 X、北坐标作 Y，也与测点导入一致。
    """
    rows = _read_rows(filepath)
    first_data = next((index for index, row in enumerate(rows) if _numeric_count(row) >= 2), None)
    if first_data is None:
        raise ValueError("文件里没有找到坐标数据（每行至少要有 X、Y 两个数字）")
    header = rows[first_data - 1] if first_data > 0 else None
    data = rows[first_data:]
    is_dat = Path(filepath).suffix.lower() == ".dat"

    columns = _columns_from_header(header) if header is not None else None
    result = BoundaryPoints()
    if columns is not None:
        names = [_text(cell) for cell in header]
        result.layout = "按表头：" + "、".join(
            f"{label}={names[index]}" for label, index in zip(("点号", "X", "Y"), columns) if index is not None
        )
    elif is_dat or sum(_looks_like_cass(row) for row in data) > len(data) / 2:
        columns = None
        result.layout = "CASS 格式：点号, 编码, 东(X), 北(Y), 高程"
    else:
        columns = _columns_by_position(data)
        used = 3 if columns[0] is not None else 2
        result.layout = ("点号、X、Y" if columns[0] is not None else "X、Y") + (
            "（其余列未使用）" if len(data[0]) > used else ""
        )

    points: List[XY] = []
    ids: List[str] = []
    for offset, row in enumerate(data):
        if columns is not None:
            id_column, x_column, y_column = columns
        elif len(row) >= 5:
            id_column, x_column, y_column = 0, 2, 3
        elif len(row) == 4:
            id_column, x_column, y_column = 0, 1, 2
        else:
            id_column, x_column, y_column = None, 0, 1
        x = _number(row[x_column]) if x_column < len(row) else None
        y = _number(row[y_column]) if y_column < len(row) else None
        if x is None or y is None:
            result.skipped.append(f"第 {first_data + offset + 1} 行: X 或 Y 为空或不是数字")
            continue
        points.append((x, y))
        ids.append(_text(row[id_column]) if id_column is not None and id_column < len(row) else "")

    result.points, result.ids = _tidy(points, ids)
    if len(result.points) < 3:
        raise ValueError(f"只读到 {len(result.points)} 个边界点（{result.layout}），边界至少需要 3 个点")
    return result


def _natural_key(text: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


class _PointIndex:
    """按点号找测点：先精确匹配，再忽略大小写和 Excel 带出的“.0”。"""

    def __init__(self, points: Sequence[SurveyPoint]):
        self.points = list(points)
        self.exact: Dict[str, List[SurveyPoint]] = {}
        self.loose: Dict[str, List[SurveyPoint]] = {}
        for point in self.points:
            point_id = str(point.id).strip()
            self.exact.setdefault(point_id, []).append(point)
            self.loose.setdefault(self._loose_key(point_id), []).append(point)

    @staticmethod
    def _loose_key(point_id: str) -> str:
        return re.sub(r"\.0+$", "", point_id).lower()

    def find(self, point_id: str) -> Optional[SurveyPoint]:
        matches = self.exact.get(point_id) or self.loose.get(self._loose_key(point_id))
        if not matches:
            return None
        if len(matches) > 1:
            raise ValueError(f"点号 {point_id} 在测点里有 {len(matches)} 个，无法确定用哪一个；请先在数据导入页改掉重复点号")
        return matches[0]

    def matching(self, pattern: str) -> List[SurveyPoint]:
        found = [point for point in self.points if fnmatch.fnmatchcase(str(point.id).strip().lower(), pattern.lower())]
        return sorted(found, key=lambda point: _natural_key(str(point.id)))


def _expand_range(token: str, index: _PointIndex) -> Optional[List[SurveyPoint]]:
    """“12-18”“B3~B9”“B3-9”这样的区间；不是区间时返回 None。"""
    for position, char in enumerate(token):
        if char not in RANGE_SEPARATORS or position == 0 or position == len(token) - 1:
            continue
        left, right = token[:position].strip(), token[position + 1:].strip()
        left_match = re.fullmatch(r"(.*?)(\d+)", left)
        right_match = re.fullmatch(r"(.*?)(\d+)", right)
        if not left_match or not right_match:
            continue
        prefix = left_match.group(1)
        if right_match.group(1) not in ("", prefix):
            continue
        start, end = int(left_match.group(2)), int(right_match.group(2))
        if abs(end - start) + 1 > MAX_RANGE_POINTS:
            raise ValueError(f"区间 {token} 太大，请检查点号")
        width = len(left_match.group(2))
        found: List[SurveyPoint] = []
        missing: List[str] = []
        for number in range(start, end + (1 if end >= start else -1), 1 if end >= start else -1):
            point = index.find(f"{prefix}{number}") or index.find(f"{prefix}{str(number).zfill(width)}")
            if point is None:
                missing.append(f"{prefix}{number}")
            else:
                found.append(point)
        if missing:
            shown = "、".join(missing[:8]) + (" 等" if len(missing) > 8 else "")
            raise ValueError(f"区间 {token} 里没有点号 {shown}；这些点如果本来就没有，请把区间拆开写")
        return found
    return None


def parse_point_sequence(text: str, points: Sequence[SurveyPoint]) -> List[SurveyPoint]:
    """把“5,6,7,12-18,B*”这样的点号串解析成按顺序排列的测点，用来连成边界。

    - 逗号、顿号、分号或空格分隔
    - “12-18”“B3~B9”是区间，按点号里的数字依次展开，也可以倒着写（18-12）
    - “B*”“边界?”是通配，匹配到的点按点号的自然顺序排列
    """
    tokens = [token for token in re.split(r"[,，、;；\s]+", text.strip()) if token]
    if not tokens:
        raise ValueError("请输入边界点的点号，如 5,6,7,12-18")
    index = _PointIndex(points)

    sequence: List[SurveyPoint] = []
    for token in tokens:
        point = index.find(token)
        if point is not None:
            sequence.append(point)
            continue
        if "*" in token or "?" in token:
            matched = index.matching(token)
            if not matched:
                raise ValueError(f"没有点号符合 {token}")
            sequence.extend(matched)
            continue
        expanded = _expand_range(token, index)
        if expanded is None:
            raise ValueError(f"测点里没有点号 {token}")
        sequence.extend(expanded)

    ordered: List[SurveyPoint] = []
    for point in sequence:
        if ordered and ordered[-1] is point:
            continue
        ordered.append(point)
    if len(ordered) >= 2 and ordered[0] is ordered[-1]:
        ordered.pop()   # 末尾又写了一遍起点，表示闭合
    seen = set()
    for point in ordered:
        if id(point) in seen:
            raise ValueError(f"点号 {point.id} 出现了两次，边界不能两次经过同一个点")
        seen.add(id(point))
    if len(ordered) < 3:
        raise ValueError(f"只有 {len(ordered)} 个点，边界至少需要 3 个点")
    return ordered
