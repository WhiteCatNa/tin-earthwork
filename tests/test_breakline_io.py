"""特征线的 DXF 导入与工程文件保存"""
import json

import pytest

from core.breaklines import Breakline
from core.calculator import SurveyPoint, TINEarthworkCalculator
from utils.data_handler import load_project, save_project
from utils.dxf_io import import_boundary_from_dxf, import_breaklines_from_dxf, list_dxf_layers


def _pairs(*items):
    return "\n".join(f"{code}\n{value}" for code, value in items)


def _lwpolyline(layer, points, closed=False, elevation=None):
    items = [(0, "LWPOLYLINE"), (8, layer), (90, len(points)), (70, 1 if closed else 0)]
    if elevation is not None:
        items.append((38, elevation))
    for x, y in points:
        items += [(10, x), (20, y)]
    return _pairs(*items)


def _polyline_3d(layer, points):
    items = [(0, "POLYLINE"), (8, layer), (66, 1), (10, 0), (20, 0), (30, 0), (70, 8)]
    for x, y, z in points:
        items += [(0, "VERTEX"), (8, layer), (10, x), (20, y), (30, z), (70, 32)]
    items.append((0, "SEQEND"))
    return _pairs(*items)


def _line(layer, a, b):
    return _pairs((0, "LINE"), (8, layer), (10, a[0]), (20, a[1]), (30, a[2]), (11, b[0]), (21, b[1]), (31, b[2]))


def _dxf(tmp_path, *entities):
    path = tmp_path / "lines.dxf"
    body = "\n".join(entities)
    path.write_text(_pairs((0, "SECTION"), (2, "ENTITIES")) + "\n" + body + "\n" + _pairs((0, "ENDSEC"), (0, "EOF")),
                    encoding="utf-8")
    return path


def test_dxf_breaklines_by_layer(tmp_path):
    path = _dxf(
        tmp_path,
        _lwpolyline("红线", [(0, 0), (100, 0), (100, 60), (0, 60)], closed=True),
        _lwpolyline("坡顶", [(0, 20), (50, 20), (100, 20)]),
        _lwpolyline("坡脚", [(0, 22), (100, 22)], elevation=8.5),
        _polyline_3d("沟底", [(0, 24, 7.0), (50, 24, 7.5), (100, 24, 8.0)]),
        _line("沟底", (10, 30, 9.0), (20, 30, 9.5)),
        _line("坡顶", (10, 40, 0), (20, 40, 0)),
    )
    assert list_dxf_layers(path) == {"坡脚": 1, "坡顶": 2, "沟底": 2, "红线": 1}
    # 边界导入不受影响
    assert import_boundary_from_dxf(path) == [(0, 0), (100, 0), (100, 60), (0, 60)]

    lines = import_breaklines_from_dxf(path, layers=["坡顶", "坡脚", "沟底"])
    by_layer = {}
    for line in lines:
        by_layer.setdefault(line.name.rsplit("-", 1)[0], []).append(line.vertices)
    assert by_layer["坡顶"] == [[(0, 20, None), (50, 20, None), (100, 20, None)], [(10, 40, None), (20, 40, None)]]
    assert by_layer["坡脚"] == [[(0, 22, 8.5), (100, 22, 8.5)]]
    assert by_layer["沟底"] == [[(0, 24, 7.0), (50, 24, 7.5), (100, 24, 8.0)], [(10, 30, 9.0), (20, 30, 9.5)]]


def test_dxf_closed_polyline_closes_ring_and_all_layers(tmp_path):
    path = _dxf(tmp_path, _lwpolyline("坎", [(0, 0), (10, 0), (10, 10)], closed=True))
    [line] = import_breaklines_from_dxf(path)
    assert line.vertices[0] == line.vertices[-1] and len(line.vertices) == 4


def test_dxf_without_lines_on_layer(tmp_path):
    path = _dxf(tmp_path, _lwpolyline("红线", [(0, 0), (1, 0), (1, 1)], closed=True))
    with pytest.raises(ValueError, match="没有多段线或直线"):
        import_breaklines_from_dxf(path, layers=["坡顶"])


def test_dxf_breaklines_drive_calculation(tmp_path):
    points = [SurveyPoint(f"P{i}", float(x), float(y), 10.0)
              for i, (x, y) in enumerate([(0, 0), (10, 0), (10, 10), (0, 10), (4, 6), (6, 4)])]
    path = _dxf(tmp_path, _lwpolyline("坎", [(0, 0), (10, 10)]))
    calc = TINEarthworkCalculator(design_elevation=9.0)
    calc.add_points(points)
    calc.set_breaklines(import_breaklines_from_dxf(path))
    result = calc.run_full_calculation()
    assert result.total_cut == pytest.approx(100.0)


def test_project_saves_breaklines(tmp_path):
    path = tmp_path / "site.tinproj.json"
    before = [Breakline([(0, 20, None), (100, 20, None)], name="坡顶"), Breakline([(0, 24, 7.0), (100, 24, 8.0)])]
    after = [Breakline([(5, 5, 1.0), (6, 6, 2.0)], name="后期坎")]
    save_project(path, [SurveyPoint("A", 0, 0, 1)], [], breaklines=before, compare_breaklines=after)
    assert json.loads(path.read_text(encoding="utf-8"))["format_version"] == 3
    data = load_project(path)
    assert data["breaklines"] == before
    assert data["compare_breaklines"] == after


def test_project_without_breaklines_stays_v2(tmp_path):
    path = tmp_path / "site.tinproj.json"
    save_project(path, [SurveyPoint("A", 0, 0, 1)], [])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["format_version"] == 2 and "breaklines" not in payload
    data = load_project(path)
    assert data["breaklines"] == [] and data["compare_breaklines"] == []


def test_reports_with_breaklines(tmp_path):
    import openpyxl

    from tests.test_breaklines import EXACT_CUT, _calc, _survey
    from utils.data_handler import DataExporter

    points, lines = _survey()
    calc = _calc(points, lines=[Breakline([(x, y, None) for x, y in line], name=f"坡线{k}")
                                for k, line in enumerate(lines, 1)])
    result = calc.run_full_calculation()
    result.without_breaklines = calc.volumes_without_breaklines()
    assert result.total_cut == pytest.approx(EXACT_CUT, abs=1e-6)
    assert result.without_breaklines[0] != pytest.approx(EXACT_CUT, abs=1.0)
    # 不加特征线的对比不改动原计算器的三角网
    assert calc.calculate_volumes().total_cut == pytest.approx(EXACT_CUT, abs=1e-6)

    exporter = DataExporter("沟")
    rows = {row[0]: row for row in exporter.summary_rows(result)}
    assert rows["特征线"][1] == 4
    assert rows["不加特征线挖方"][1] == round(result.without_breaklines[0], 3)

    excel = tmp_path / "report.xlsx"
    exporter.export_summary_excel(result, str(excel), points)
    book = openpyxl.load_workbook(excel)
    assert "特征线" in book.sheetnames
    sheet = list(book["特征线"].values)
    assert sheet[0] == ("序号", "名称", "期次", "顶点", "X", "Y", "高程")
    assert len(sheet) - 1 == sum(len(line) for line in lines)
    assert "约束Delaunay" in str(list(book["土方量汇总表"].values))

    dxf = tmp_path / "result.dxf"
    exporter.export_boundary_dxf(result, str(dxf))
    text = dxf.read_text(encoding="utf-8")
    assert text.count("\nBREAKLINE\n") > 0
    assert text.count("0\n3DFACE\n8\nTIN\n") == result.triangle_count
    assert import_boundary_from_dxf(dxf) == [(0, 0), (100, 0), (100, 60), (0, 60)]

    report = exporter.generate_report_text(result, len(points))
    assert "4 条特征线参与构网" in report and "不加特征线时挖方" in report
