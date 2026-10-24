"""地形图（高程点、注记、等高线）与 LAS 点云导入。"""
import struct

import numpy as np
import pytest

from core.calculator import TINEarthworkCalculator
from utils.data_handler import DataImporter
from utils.terrain_import import (KIND_CONTOUR, KIND_LINE3D, KIND_POINT, KIND_TEXT, THIN_CENTER, THIN_LOWEST,
                                  build_terrain_points, cloud_points, default_selection, densify, read_las,
                                  scan_dxf_terrain, suggest_cell, thin_cloud)


def _dxf(entities: str) -> str:
    return f"0\nSECTION\n2\nENTITIES\n{entities}0\nENDSEC\n0\nEOF\n"


def _lw(layer, z, vertices, closed=False):
    body = "".join(f"10\n{x}\n20\n{y}\n" for x, y in vertices)
    return f"0\nLWPOLYLINE\n8\n{layer}\n90\n{len(vertices)}\n70\n{1 if closed else 0}\n38\n{z}\n{body}"


def _polyline(layer, flags, vertices, elevation=0.0, vertex_flags=None):
    out = f"0\nPOLYLINE\n8\n{layer}\n66\n1\n10\n0\n20\n0\n30\n{elevation}\n70\n{flags}\n"
    for k, (x, y, z) in enumerate(vertices):
        vf = (vertex_flags or [0] * len(vertices))[k]
        out += f"0\nVERTEX\n8\n{layer}\n10\n{x}\n20\n{y}\n30\n{z}\n70\n{vf}\n"
    return out + f"0\nSEQEND\n8\n{layer}\n"


def test_scan_dxf_terrain(tmp_path):
    entities = (
        "0\nPOINT\n8\nGCD\n10\n1\n20\n1\n30\n10.5\n"
        "0\nINSERT\n8\nGCD\n2\nGC200\n10\n2\n20\n2\n30\n10.6\n"
        "0\nINSERT\n8\nTREE\n2\nTREE\n10\n3\n20\n3\n30\n0\n"                  # Z = 0 的符号块不算
        "0\nTEXT\n8\nGCZJ\n10\n1.2\n20\n1.2\n30\n0\n1\n10.50\n"
        "0\nTEXT\n8\nGCZJ\n10\n5\n20\n5\n30\n0\n1\n张家村\n"                   # 非数字注记不算
        "0\nMTEXT\n8\nGCZJ\n10\n6\n20\n6\n30\n0\n1\n{\\fSimSun|b0;11.25}\n"
        + _lw("DGX", 10, [(0, 0), (0, 20)])
        + _lw("DGX", 11, [(10, 0), (10, 20)])
        + _polyline("DGX", 4, [(20, 0, 0), (20, 7, 0), (20, 20, 0)], elevation=12, vertex_flags=[0, 16, 0])
        + _polyline("ROAD", 8, [(0, 30, 9), (20, 30, 13)])
        + _polyline("MESH", 16, [(0, 0, 1), (1, 0, 1)])
    )
    path = tmp_path / "dem.dxf"
    path.write_text(_dxf(entities), encoding="utf-8")
    sources = scan_dxf_terrain(str(path))
    kinds = {(s.layer, s.kind): s for s in sources}
    assert set(kinds) == {("GCD", KIND_POINT), ("GCZJ", KIND_TEXT), ("DGX", KIND_CONTOUR), ("ROAD", KIND_LINE3D)}
    assert kinds[("GCZJ", KIND_TEXT)].points == [(1.2, 1.2, 10.5), (6.0, 6.0, 11.25)]
    contour = kinds[("DGX", KIND_CONTOUR)]
    assert contour.count == 3 and contour.lines[2] == [(20.0, 0.0, 12.0), (20.0, 20.0, 12.0)]   # 控制点已去掉
    assert default_selection(sources) == [True, False, True, True]     # 有高程点时注记默认不选
    assert "高程 10 ~ 12 m" in contour.description


def test_tin_from_contours_is_exact(tmp_path):
    entities = "".join(_lw("DGX", 10 + k, [(10 * k, 0), (10 * k, 20)]) for k in range(3))
    path = tmp_path / "contours.dxf"
    path.write_text(_dxf(entities), encoding="utf-8")
    sources = scan_dxf_terrain(str(path))
    built = build_terrain_points(sources, [True], spacing=5)
    assert len(built.points) == 15 and built.flat_triangles == 0
    calc = TINEarthworkCalculator(11.0)
    calc.add_points(built.points)
    result = calc.run_full_calculation()
    assert result.total_cut == pytest.approx(100) and result.total_fill == pytest.approx(100)

    points, issues, frame = DataImporter.import_file(str(path))     # 不经界面：默认选项
    assert len(points) == 15 and list(frame.columns) == ["点号", "X", "Y", "Z"]   # 默认间距 5 m，每条线 5 点


def test_flat_triangles_and_conflicts(tmp_path):
    # 一条闭合等高线围成的山顶：内部没有点，三角形全落在同一条线上
    ring = [(0, 0), (10, 0), (10, 10), (0, 10)]
    path = tmp_path / "hill.dxf"
    path.write_text(_dxf(_lw("DGX", 5, ring, closed=True) + "0\nPOINT\n8\nGCD\n10\n0\n20\n0\n30\n6\n"),
                    encoding="utf-8")
    sources = scan_dxf_terrain(str(path))
    built = build_terrain_points(sources, [True, True], spacing=100)
    assert built.conflicts == 1                       # (0,0) 高程点与等高线顶点重合、高程不同
    assert built.triangle_count == 2 and built.flat_triangles == 1    # 含 (0,0) 高程点的三角形不平
    assert built.flat_ratio == 0.5
    with pytest.raises(ValueError, match="至少选择"):
        build_terrain_points(sources, [False, False])


def test_densify():
    line = [(0, 0, 1), (10, 0, 1), (10, 3, 1)]
    out = densify(line, 4)
    assert out[0] == (0, 0, 1) and out[-1] == (10, 3, 1) and len(out) == 1 + 3 + 1


def _write_las(path, xyz, classes, version=(1, 2), point_format=1):
    record = {1: 28, 0: 20, 6: 30}[point_format]
    header_size = 375 if version >= (1, 4) else 227
    scale, offset = (0.001, 0.001, 0.001), (1000.0, 2000.0, 0.0)
    header = bytearray(header_size)
    header[0:4] = b"LASF"
    header[24], header[25] = version
    struct.pack_into("<H", header, 94, header_size)
    struct.pack_into("<I", header, 96, header_size)
    header[104] = point_format
    struct.pack_into("<H", header, 105, record)
    struct.pack_into("<I", header, 107, 0 if version >= (1, 4) else len(xyz))
    struct.pack_into("<3d", header, 131, *scale)
    struct.pack_into("<3d", header, 155, *offset)
    if version >= (1, 4):
        struct.pack_into("<Q", header, 247, len(xyz))
    body = bytearray()
    for (x, y, z), cls in zip(xyz, classes):
        rec = bytearray(record)
        struct.pack_into("<3i", rec, 0, *(round((v - o) / s) for v, o, s in zip((x, y, z), offset, scale)))
        rec[16 if point_format >= 6 else 15] = cls
        body += rec
    path.write_bytes(bytes(header) + bytes(body))


@pytest.mark.parametrize("version,fmt", [((1, 2), 1), ((1, 2), 0), ((1, 4), 6)])
def test_read_las(tmp_path, version, fmt):
    rng = np.random.default_rng(1)
    xyz = np.column_stack([1000 + rng.uniform(0, 50, 500), 2000 + rng.uniform(0, 50, 500), rng.uniform(5, 6, 500)])
    classes = np.where(np.arange(500) % 5 == 0, 5, 2)
    path = tmp_path / "cloud.las"
    _write_las(path, xyz, classes, version, fmt)
    cloud = read_las(str(path))
    assert cloud.count == 500 and cloud.has_ground_class
    assert np.allclose(cloud.xyz, xyz, atol=6e-4)
    assert (cloud.classification == classes).all()
    points = cloud_points(cloud, 10, THIN_LOWEST, ground_only=True)
    assert len(points) <= 25
    ground = xyz[classes == 2]
    # 每格最低点：任何一个保留点都是所在格内地面点中最低的
    for p in points:
        cx, cy = np.floor((p.x - ground[:, 0].min()) / 10), np.floor((p.y - ground[:, 1].min()) / 10)
        same = (np.floor((ground[:, 0] - ground[:, 0].min()) / 10) == cx) & \
               (np.floor((ground[:, 1] - ground[:, 1].min()) / 10) == cy)
        assert p.z == pytest.approx(ground[same, 2].min(), abs=6e-4)
    imported, _, _ = DataImporter.import_file(str(path))
    assert len(imported) > 3


def test_laz_and_bad_files(tmp_path):
    path = tmp_path / "x.las"
    _write_las(path, [(1000, 2000, 1)] * 3, [2] * 3)
    data = bytearray(path.read_bytes())
    data[104] |= 0x80
    path.write_bytes(bytes(data))
    with pytest.raises(ValueError, match="LAZ"):
        read_las(str(path))
    path.write_bytes(b"not a las file" * 30)
    with pytest.raises(ValueError, match="不是 LAS"):
        read_las(str(path))


def test_thinning():
    xyz = np.array([(0.1, 0.1, 5), (0.9, 0.9, 3), (0.5, 0.45, 9), (1.5, 0.5, 1)], dtype=float)
    assert sorted(thin_cloud(xyz, 1.0, THIN_LOWEST).tolist()) == [1, 3]
    assert sorted(thin_cloud(xyz, 1.0, THIN_CENTER).tolist()) == [2, 3]
    assert suggest_cell(np.array([(0, 0, 0), (1000, 1000, 0)]), 10_000) == 10
    with pytest.raises(ValueError):
        thin_cloud(xyz, 0)
