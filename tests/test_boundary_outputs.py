"""边界的录入与成果：粘贴坐标、导出边界、报告里的边界坐标表、DXF 底图。"""
import math

import openpyxl
import pytest

from core.calculator import SurveyPoint, TINEarthworkCalculator
from utils.boundary_io import (
    boundary_table, find_point, read_boundary_points, read_boundary_text, vertex_ids, write_boundary_csv,
)
from utils.data_handler import DataExporter
from utils.dxf_io import import_boundary_from_dxf, read_dxf_backdrop

POINTS = [SurveyPoint(f"P{ix}{iy}", ix * 10.0, iy * 10.0, 10 + 0.1 * ix) for ix in range(5) for iy in range(5)]
RING = [(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (15.5, 35.0)]      # 最后一点不是测点


def _result(ring=RING):
    calculator = TINEarthworkCalculator(9.0)
    calculator.add_points(POINTS)
    calculator.set_boundary(ring)
    return calculator.run_full_calculation()


def test_paste_text_copied_from_excel():
    text = "点号\tX\tY\nJ1\t0\t0\nJ2\t40\t0\nJ3\t40\t30\n\n"
    result = read_boundary_text(text)
    assert result.points == [(0.0, 0.0), (40.0, 0.0), (40.0, 30.0)]
    assert result.ids == ["J1", "J2", "J3"]
    # 微信里复制来的，逗号、空格混用也行
    assert read_boundary_text("1, 0, 0\n2, 40, 0\n3, 40, 30").points == result.points
    with pytest.raises(ValueError, match="没有找到坐标数据"):
        read_boundary_text("今天的边界点还没测")


def test_vertex_ids_and_table():
    assert vertex_ids(RING, POINTS) == ["P00", "P40", "P43", ""]
    table = boundary_table(RING, POINTS)
    assert table[0] == [1, "P00", 0.0, 0.0, 40.0]
    assert table[1][4] == 30.0
    assert table[-1][1] == "" and table[-1][4] == pytest.approx(math.hypot(15.5, 35.0), abs=1e-3)


def test_find_point():
    assert find_point("p43", POINTS).id == "P43"
    assert find_point("X9", POINTS) is None


def test_csv_export_reads_back_the_same_boundary(tmp_path):
    path = tmp_path / "边界.csv"
    write_boundary_csv(str(path), RING, POINTS)
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")        # 带 BOM，Excel 打开不乱码
    back = read_boundary_points(str(path))
    assert back.points == RING
    assert back.ids == ["P00", "P40", "P43", ""]


def test_excel_report_has_boundary_sheet(tmp_path):
    result = _result()
    path = tmp_path / "report.xlsx"
    DataExporter("试验段").export_summary_excel(result, path, POINTS)
    sheet = openpyxl.load_workbook(path)["计算边界"]
    rows = [row for row in sheet.iter_rows(values_only=True)]
    header = rows.index(("序号", "点号", "X", "Y", "至下一点边长(m)"))
    assert rows[header + 1][:4] == (1, "P00", 0, 0)
    assert rows[header + 4][1] is None                          # 不是测点：点号空白
    perimeter = next(row for row in rows if row[0] == "周长")
    area = next(row for row in rows if row[0] == "面积")
    assert perimeter[4] == pytest.approx(40 + 30 + math.hypot(24.5, 5) + math.hypot(15.5, 35), abs=1e-3)
    assert area[4] == pytest.approx(result.boundary_area, abs=1e-3)


def test_pdf_report_has_boundary_pages(tmp_path):
    exporter = DataExporter("试验段")
    result = _result()
    assert [page.axes[0].get_title(loc="left").splitlines()[0] for page in exporter._boundary_pages(RING, POINTS, result)] \
        == ["试验段  计算边界坐标表"]
    # 顶点多时分页；再多就指向 Excel
    many = [(20 + 15 * math.cos(k * 2 * math.pi / 90), 20 + 15 * math.sin(k * 2 * math.pi / 90)) for k in range(90)]
    assert len(exporter._boundary_pages(many, POINTS, _result(many))) == 3
    lots = [(20 + 15 * math.cos(k * 2 * math.pi / 200), 20 + 15 * math.sin(k * 2 * math.pi / 200)) for k in range(200)]
    pages = exporter._boundary_pages(lots, POINTS, _result(lots))
    assert len(pages) == 1 and "Excel" in pages[0].axes[0].texts[0].get_text()

    path = tmp_path / "计算书.pdf"
    assert exporter.export_pdf_report(result, len(POINTS), POINTS, result.boundary_points, path)
    assert path.read_bytes().count(b"/Type /Page") >= 3 or b"/Count 3" in path.read_bytes()


def test_dxf_exports_label_boundary_points(tmp_path):
    result = _result()
    path = tmp_path / "成果.dxf"
    DataExporter().export_boundary_dxf(result, path, POINTS)
    text = path.read_text(encoding="utf-8")
    assert text.count("\nBOUNDARY_POINT\n") == 4
    for label in ("P00", "P40", "P43", "\n4\n"):
        assert label in text                                    # 不是测点的顶点标序号
    assert import_boundary_from_dxf(path) == RING

    only = tmp_path / "边界.dxf"
    DataExporter().export_boundary_only_dxf(RING, only, POINTS)
    assert import_boundary_from_dxf(only) == RING
    assert "ZERO_CONTOUR" not in only.read_text(encoding="utf-8")


def test_dxf_labels_avoid_chinese_ids_and_huge_boundaries(tmp_path):
    points = [SurveyPoint("角点", 0.0, 0.0, 1.0), SurveyPoint("B2", 10.0, 0.0, 1.0), SurveyPoint("B3", 0.0, 10.0, 1.0)]
    path = tmp_path / "cn.dxf"
    DataExporter().export_boundary_only_dxf([(0.0, 0.0), (10.0, 0.0), (0.0, 10.0)], path, points)
    text = path.read_text(encoding="utf-8")
    assert "角点" not in text and "\n1\n" in text and "B2" in text
    ring = [(math.cos(k / 100), math.sin(k / 100)) for k in range(600)]
    assert DataExporter._dxf_boundary_labels(ring, []) == []


def test_backdrop_reads_polylines_and_lines(tmp_path):
    bulge = math.tan(math.pi / 8)
    text = (
        "0\nSECTION\n2\nENTITIES\n"
        "0\nLWPOLYLINE\n8\n红线\n90\n4\n70\n1\n10\n0\n20\n0\n10\n100\n20\n0\n42\n" + repr(bulge) +
        "\n10\n120\n20\n20\n10\n0\n20\n20\n"
        "0\nLINE\n8\n道路\n10\n-10\n20\n5\n30\n0\n11\n130\n21\n5\n31\n0\n"
        "0\nLWPOLYLINE\n8\n房屋\n90\n2\n70\n0\n10\n30\n20\n8\n10\n50\n20\n8\n"
        "0\nENDSEC\n0\nEOF\n"
    )
    path = tmp_path / "底图.dxf"
    path.write_text(text, encoding="utf-8")
    backdrop = read_dxf_backdrop(path)
    assert [item.layer for item in backdrop] == ["红线", "房屋", "道路"]
    assert backdrop[0].closed and backdrop[0].arc_count == 1 and len(backdrop[0].points) > 4
    assert backdrop[2].points == [(-10.0, 5.0), (130.0, 5.0)]
