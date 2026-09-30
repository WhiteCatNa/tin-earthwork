"""
设计面与 TIN 曲面：斜面设计面、在三角网上任意位置线性插值。
"""
from dataclasses import dataclass
from typing import Dict

import numpy as np
from scipy.spatial import ConvexHull, Delaunay, cKDTree


@dataclass
class PlaneDesign:
    """斜面设计面：H = H0 + ix·(x − X0) + iy·(y − Y0)。

    坡度以 % 计，沿 +X / +Y 方向升高为正（场平常用的“双向坡度”）。
    平面在三角形内是线性的，所以 TIN 法按顶点高差线性插值对斜面仍是精确的。
    """
    x0: float
    y0: float
    h0: float
    slope_x: float = 0.0
    slope_y: float = 0.0

    def elevation(self, x, y):
        return self.h0 + self.slope_x / 100.0 * (x - self.x0) + self.slope_y / 100.0 * (y - self.y0)

    def describe(self) -> str:
        return (
            f"斜面：基准点 ({self.x0:.3f}, {self.y0:.3f}) 高程 {self.h0:.3f} m，"
            f"X 向坡度 {self.slope_x:+.3f}%，Y 向坡度 {self.slope_y:+.3f}%"
        )

    def to_dict(self) -> Dict[str, float]:
        return {"x0": self.x0, "y0": self.y0, "h0": self.h0, "slope_x": self.slope_x, "slope_y": self.slope_y}

    @classmethod
    def from_dict(cls, data: Dict[str, float]) -> "PlaneDesign":
        return cls(
            x0=float(data["x0"]),
            y0=float(data["y0"]),
            h0=float(data["h0"]),
            slope_x=float(data.get("slope_x", 0.0)),
            slope_y=float(data.get("slope_y", 0.0)),
        )


class TinSurface:
    """由 Delaunay 三角网定义的分片线性曲面（局部坐标），可在任意位置插值。

    values 中每个数组与 delaunay.points 一一对应；网外位置返回 NaN。
    """

    def __init__(self, delaunay: Delaunay, values: Dict[str, np.ndarray]):
        self.delaunay = delaunay
        self.values = {key: np.asarray(value, dtype=float) for key, value in values.items()}

    @property
    def triangles_xy(self) -> np.ndarray:
        return self.delaunay.points[self.delaunay.simplices]

    def _evaluate(self, xy: np.ndarray, simplex: np.ndarray, key: str) -> np.ndarray:
        """按给定三角形的平面求值；点在三角形外时即为该平面的线性外推。"""
        transform = self.delaunay.transform[simplex]
        delta = xy - transform[:, 2]
        bary = np.einsum("ijk,ik->ij", transform[:, :2], delta)
        weights = np.column_stack([bary, 1 - bary.sum(axis=1)])
        return np.einsum("ij,ij->i", weights, self.values[key][self.delaunay.simplices[simplex]])

    def interpolate(self, xy: np.ndarray, key: str) -> np.ndarray:
        xy = np.asarray(xy, dtype=float).reshape(-1, 2)
        simplex = self.delaunay.find_simplex(xy)
        result = np.full(len(xy), np.nan)
        inside = simplex >= 0
        if inside.any():
            result[inside] = self._evaluate(xy[inside], simplex[inside], key)
        return result

    # 网外外推时参与拟合平面的最近测点数
    EXTRAPOLATION_NEIGHBORS = 8

    def extrapolate(self, xy: np.ndarray, key: str) -> np.ndarray:
        """网内线性插值；网外用最近几个测点的最小二乘平面外推。

        不用最近三角形的平面：凸包边缘常有细长三角形，外推会得到离谱的高程。
        最小二乘平面在平面地形下仍精确；近邻共线时退回最近测点的高程。
        """
        xy = np.asarray(xy, dtype=float).reshape(-1, 2)
        result = self.interpolate(xy, key)
        outside = np.nonzero(np.isnan(result))[0]
        if not len(outside):
            return result
        points = self.delaunay.points
        values = self.values[key]
        count = min(self.EXTRAPOLATION_NEIGHBORS, len(points))
        _, neighbors = cKDTree(points).query(xy[outside], k=count)
        neighbors = np.asarray(neighbors).reshape(len(outside), count)
        for row, index in zip(outside, neighbors):
            offsets = points[index] - xy[row]
            design = np.column_stack([offsets, np.ones(count)])
            coefficients, _, rank, _ = np.linalg.lstsq(design, values[index], rcond=None)
            result[row] = coefficients[2] if rank == 3 else values[index[0]]
        return result

    def hull(self) -> np.ndarray:
        """三角网范围（凸包），逆时针顶点。"""
        return self.delaunay.points[ConvexHull(self.delaunay.points).vertices]
