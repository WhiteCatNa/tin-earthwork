"""设计三角网：导入设计点（DXF 高程点、3DFACE、设计等高线）作为设计面。"""
import pytest

from core.calculator import COMPARE_DESIGN, SurveyPoint, TINEarthworkCalculator
from core.contours import trace_contours
from utils.data_handler import DataExporter, load_project, report_labels, save_project
from utils.design_import import import_design_points_from_dxf, read_design_points


def grid(func, step=10.0, size=100.0, prefix="P"):
    n = int(size / step) + 1
    return [SurveyPoint(f"{prefix}{i}-{j}", i * step, j * step, func(i * step, j * step))
            for i in range(n) for j in range(n)]


def _dxf(entities: str) -> str:
    return f"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n{entities}0\nENDSEC\n0\nEOF\n"


def _point(x, y, z):
    return f"0\nPOINT\n8\nGCD\n10\n{x}\n20\n{y}\n30\n{z}\n"


def test_read_design_points_from_dxf(tmp_path):
    entities = (
        _point(0, 0, 10) + _point(0, 0, 10)                                   # 重复点只留一个
        + "0\n3DFACE\n8\nDESIGN\n10\n100\n20\n0\n30\n11\n11\n100\n21\n100\n31\n11\n12\n0\n22\n100\n32\n10\n13\n0\n23\n100\n33\n10\n"
        + "0\nLWPOLYLINE\n8\nDGX\n90\n2\n38\n10.5\n10\n50\n20\n0\n10\n50\n20\n100\n"   # 设计等高线（标高 10.5）
        + "0\nPOLYLINE\n8\nDGX\n66\n1\n70\n8\n0\nVERTEX\n8\nDGX\n10\n20\n20\n0\n30\n10.2\n"
        + "0\nVERTEX\n8\nDGX\n10\n20\n20\n100\n30\n10.2\n0\nSEQEND\n8\nDGX\n"
        + "0\nPOLYLINE\n8\nDGX\n66\n1\n30\n10.7\n70\n0\n0\nVERTEX\n8\nDGX\n10\n70\n20\n0\n30\n0\n"
        + "0\nVERTEX\n8\nDGX\n10\n70\n20\n100\n30\n0\n0\nSEQEND\n8\nDGX\n"
        + "0\nINSERT\n8\nTREE\n2\nTREE\n10\n5\n20\n5\n30\n0\n"                    # Z = 0 的块不是高程点
        + "0\nINSERT\n8\nGCD\n2\nGC200\n10\n60\n20\n60\n30\n10.6\n"
        + "0\nLINE\n8\n0\n10\n1\n20\n1\n30\n0\n11\n2\n21\n2\n31\n0\n"               # 平面线不读
    )
    path = tmp_path / "design.dxf"
    path.write_text(_dxf(entities), encoding="utf-8")
    raw = read_design_points(str(path))
    assert (50.0, 0.0, 10.5) in raw and (20.0, 100.0, 10.2) in raw and (70.0, 100.0, 10.7) in raw
    assert (60.0, 60.0, 10.6) in raw
    assert not any(z == 0 for _, _, z in raw)
    points = import_design_points_from_dxf(str(path))
    assert len(points) == len(raw) - 2      # (0,0,10) 重复、3DFACE 第 4 点与第 3 点... 都去重
    assert points[0].id == "D1"


def test_too_few_design_points(tmp_path):
    path = tmp_path / "few.dxf"
    path.write_text(_dxf(_point(0, 0, 1) + _point(1, 0, 1)), encoding="utf-8")
    with pytest.raises(ValueError, match="不足 3 个"):
        import_design_points_from_dxf(str(path))


def test_design_tin_volume_and_labels():
    calc = TINEarthworkCalculator()
    calc.add_points(grid(lambda x, y: 12.0))
    calc.set_compare_points(grid(lambda x, y: 10 + 0.01 * x, step=25, prefix="D"), kind=COMPARE_DESIGN)
    result = calc.run_full_calculation()
    assert result.is_design_tin
    assert result.total_cut == pytest.approx(20000 - 5000, rel=1e-9)
    labels = report_labels(result)
    assert labels["natural"] == "实测高程" and labels["design_item"] == "设计面"
    assert "设计三角网" in result.design_text
    rows = {row[0]: row for row in DataExporter.summary_rows(result)}
    assert "设计点数" in rows and rows["设计面"][1] == "设计三角网"
    assert "实测点数" in DataExporter("测试").generate_report_text(result, 121)
    assert any(p.id.startswith("设计-") for tri in result.triangles for p in tri.vertex_points)
    contours = trace_contours(calc, 0.25)
    assert sorted({c.level for c in contours.design}) == [10.0, 10.25, 10.5, 10.75, 11.0]


def test_design_tin_coverage_warning():
    calc = TINEarthworkCalculator()
    calc.add_points(grid(lambda x, y: 12.0))
    calc.set_compare_points(grid(lambda x, y: 10, step=25, size=50, prefix="D"), kind=COMPARE_DESIGN)
    result = calc.run_full_calculation()
    assert any("设计三角网只覆盖了实测范围" in w for w in result.warnings)


def test_project_round_trip_design_tin(tmp_path):
    path = tmp_path / "d.tinproj.json"
    design = grid(lambda x, y: 10, step=50, prefix="D")
    save_project(str(path), grid(lambda x, y: 12), [], design_mode="design_tin",
                 design_points=design, design_source="d.dxf", sections_enabled=True, section_spacing=20,
                 section_angle=45, contours_enabled=False, contour_interval=0.5)
    data = load_project(str(path))
    assert data["design_mode"] == "design_tin"
    assert [(p.x, p.y, p.z) for p in data["design_points"]] == [(p.x, p.y, p.z) for p in design]
    assert data["design_source"] == "d.dxf"
    assert (data["sections_enabled"], data["section_spacing"], data["section_angle"]) == (True, 20, 45)
    assert (data["contours_enabled"], data["contour_interval"]) == (False, 0.5)
