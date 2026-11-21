"""
三维视图：地面（实测/前期）与设计面（后期面）的三维三角面。

只画参与计算的范围：三角形先按计算边界裁剪；被挖空区切到的三角形细分后去掉落在挖空区内的小三角形
（仅用于显示，方量仍按精确裁剪计算）。高程夸张只改变显示比例，坐标轴刻度仍是真实高程。
"""
from typing import List, Optional, Tuple

import numpy as np
import matplotlib
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize
from matplotlib.path import Path

from core.calculator import CalculationResult, TINEarthworkCalculator
from core.geometry import linear_plane

HOLE_SUBDIVISION = 3      # 挖空区附近的三角形每条边等分 2³ = 8 份
MAX_FACES = 60000         # 超过时提示：matplotlib 三维绘制较慢


def _subdivide(tri: np.ndarray, level: int) -> np.ndarray:
    """三角形 [3, 2] 每级四等分，返回 [4^level, 3, 2]。"""
    tris = tri[None]
    for _ in range(level):
        a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
        ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
        tris = np.concatenate([np.stack(t, axis=1) for t in
                               ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca))])
    return tris


def surface_faces(calculator: TINEarthworkCalculator) -> Tuple[List[np.ndarray], List[np.ndarray]]:
    """(地面三维面, 设计面三维面)，每个面为 [k, 3] 的局部坐标顶点 (x, y, z)。须在计算之后调用。"""
    ox, oy = calculator.origin
    eps = calculator._eps * calculator._eps
    natural, design = [], []
    for tri in calculator.triangles:
        if tri.is_boundary:
            continue
        corners = np.array([(p.x - ox, p.y - oy) for p in tri.vertex_points])
        planes = []
        for key in ("z", "design_z"):
            values = [getattr(p, key) for p in tri.vertex_points]
            plane = linear_plane(*map(tuple, corners), *values, eps=eps)
            planes.append(plane if plane is not None else (0.0, 0.0, sum(values) / 3))
        region = calculator._regions.get(tri.id)
        region = corners if region is None else np.asarray(region, dtype=float)
        holes = calculator._hole_regions.get(tri.id)
        if holes:
            fan = np.array([(region[0], region[i], region[i + 1]) for i in range(1, len(region) - 1)])
            small = np.concatenate([_subdivide(t, HOLE_SUBDIVISION) for t in fan])
            centers = small.mean(axis=1)
            inside = np.zeros(len(small), dtype=bool)
            for hole in holes:
                inside |= Path(np.asarray(hole, dtype=float)).contains_points(centers)
            polygons = list(small[~inside])
        else:
            polygons = [region]
        for polygon in polygons:
            for faces, (a, b, c) in ((natural, planes[0]), (design, planes[1])):
                z = polygon[:, 0] * a + polygon[:, 1] * b + c
                faces.append(np.column_stack([polygon, z]))
    return natural, design


def draw_3d(ax, natural: List[np.ndarray], design: List[np.ndarray], result: CalculationResult,
            exaggeration: float = 1.0, show_natural: bool = True, show_design: bool = True,
            origin: Tuple[float, float] = (0.0, 0.0), cmap: str = "terrain"):
    """在 3D 坐标轴上画两个面；地面按高程着色，设计面半透明。"""
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    ax.clear()
    shown = (natural if show_natural else []) + (design if show_design else [])
    if not shown:
        ax.set_axis_off()
        return None
    all_xyz = np.concatenate(shown)
    zmin, zmax = float(all_xyz[:, 2].min()), float(all_xyz[:, 2].max())
    mappable = None
    if show_natural and natural:
        norm = Normalize(zmin, zmax if zmax > zmin else zmin + 1)
        colors = matplotlib.colormaps[cmap](norm([face[:, 2].mean() for face in natural]))
        collection = Poly3DCollection(natural, facecolors=colors, edgecolors="none", linewidths=0)
        ax.add_collection3d(collection)
        mappable = ScalarMappable(norm=norm, cmap=cmap)
    if show_design and design:
        label_design = "后期面" if result.is_compare and not result.is_design_tin else "设计面"
        collection = Poly3DCollection(design, facecolors="#4C72B0", edgecolors="#2F4A73",
                                      linewidths=0.1, alpha=0.45, label=label_design)
        ax.add_collection3d(collection)
    ax.set_axis_on()
    xmin, ymin = all_xyz[:, :2].min(axis=0)
    xmax, ymax = all_xyz[:, :2].max(axis=0)
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_zlim(zmin, zmax if zmax > zmin else zmin + 1)
    dx, dy = max(xmax - xmin, 1e-9), max(ymax - ymin, 1e-9)
    dz = max(zmax - zmin, 1e-9) * float(exaggeration)
    scale = max(dx, dy)
    ax.set_box_aspect((dx / scale, dy / scale, max(min(dz / scale, 3.0), 0.02)))
    ox, oy = origin
    ax.set_xlabel(f"X − {ox:.0f} (m)")
    ax.set_ylabel(f"Y − {oy:.0f} (m)")
    ax.set_zlabel("高程 (m)")
    return mappable


def face_count(natural: List[np.ndarray], design: List[np.ndarray]) -> int:
    return len(natural) + len(design)


def exaggeration_value(text: str) -> Optional[float]:
    """高程夸张倍数：0.1~100 的数字，其他返回 None。"""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if 0.1 <= value <= 100 else None
