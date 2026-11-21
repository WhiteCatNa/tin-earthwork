"""现场采集的边界：带点号的边界点文件、按点号连线。"""
import pandas as pd
import pytest

from core.calculator import SurveyPoint
from utils.boundary_io import parse_point_sequence, read_boundary_points
from utils.data_handler import DataImporter

SQUARE = [(500100.5, 3400200.25), (500180.5, 3400200.25), (500180.5, 3400260.75), (500100.5, 3400260.75)]


def _write(tmp_path, name, text, encoding="utf-8"):
    path = tmp_path / name
    path.write_text(text, encoding=encoding)
    return path


def _lines(rows, separator=","):
    return "\n".join(separator.join(str(cell) for cell in row) for row in rows) + "\n"


# ---------- 边界点文件 ----------

def test_headerless_id_x_y_h(tmp_path):
    rows = [(index + 1, x, y, 12.5) for index, (x, y) in enumerate(SQUARE)]
    result = read_boundary_points(_write(tmp_path, "b.csv", _lines(rows)))
    assert result.points == SQUARE
    assert result.ids == ["1", "2", "3", "4"]
    assert "点号、X、Y" in result.layout and result.skipped == []


def test_headerless_id_x_y_without_elevation(tmp_path):
    rows = [(index + 1, x, y) for index, (x, y) in enumerate(SQUARE)]
    result = read_boundary_points(_write(tmp_path, "b.txt", _lines(rows, " ")))
    assert result.points == SQUARE and result.ids == ["1", "2", "3", "4"]


def test_headerless_x_y_and_x_y_z_keep_first_two_columns(tmp_path):
    # 原有行为：没有点号时前两列就是 X、Y
    assert read_boundary_points(_write(tmp_path, "xy.csv", _lines(SQUARE))).points == SQUARE
    rows = [(x, y, 12.5 + index) for index, (x, y) in enumerate(SQUARE)]
    result = read_boundary_points(_write(tmp_path, "xyz.txt", _lines(rows, "\t")))
    assert result.points == SQUARE and result.ids == ["", "", "", ""] and result.layout.startswith("X、Y")


def test_whole_number_coordinates_are_not_mistaken_for_point_ids(tmp_path):
    # 第一列是整数但有重复，或者有 0：是坐标，不是点号
    rows = [(0, 0, 10.5), (100, 0, 10.2), (100, 80, 9.8), (0, 80, 9.9)]
    assert read_boundary_points(_write(tmp_path, "local.csv", _lines(rows))).points == [
        (0.0, 0.0), (100.0, 0.0), (100.0, 80.0), (0.0, 80.0)
    ]


def test_text_point_names_and_header_by_name(tmp_path):
    rows = [("点号", "高程", "Y", "X")] + [(f"BJ{index + 1}", 10.0, y, x) for index, (x, y) in enumerate(SQUARE)]
    result = read_boundary_points(_write(tmp_path, "named.csv", _lines(rows), encoding="gbk"))
    assert result.points == SQUARE                     # 按列名认，不看列的先后
    assert result.ids == ["BJ1", "BJ2", "BJ3", "BJ4"]
    assert "X=X" in result.layout and "Y=Y" in result.layout


def test_unrecognised_header_falls_back_to_column_content(tmp_path):
    rows = [("点名", "北坐标", "东坐标", "高")] + [(f"B{index + 1}", x, y, 3.0) for index, (x, y) in enumerate(SQUARE)]
    result = read_boundary_points(_write(tmp_path, "rtk.csv", _lines(rows)))
    assert result.points == SQUARE and result.ids == ["B1", "B2", "B3", "B4"]


def test_cass_dat_uses_east_as_x_like_the_point_importer(tmp_path):
    text = "".join(f"{index + 1},,{x},{y},8.0\n" for index, (x, y) in enumerate(SQUARE))
    path = _write(tmp_path, "bj.dat", text)
    result = read_boundary_points(path)
    assert result.points == SQUARE and "CASS" in result.layout
    # 同一个文件当测点导入，坐标一致
    points, _, _ = DataImporter.import_file(str(path))
    assert [(p.x, p.y) for p in points] == result.points


@pytest.mark.parametrize("name, text", [
    ("pts.txt", "点号 X坐标 Y坐标 实测高程\nA 1.5 2.5 10\nB 11.5 2.5 10\nC 11.5 9.5 10\nD 1.5 9.5 10\n"),
    ("pts.txt", "A 1.5 2.5 10\nB 11.5 2.5 10\nC 11.5 9.5 10\nD 1.5 9.5 10\n"),
    ("pts.csv", "点号,X,Y,高程\n7,1.5,2.5,10\n8,11.5,2.5,10\n9,11.5,9.5,10\n10,1.5,9.5,10\n"),
    ("pts.csv", "7,1.5,2.5,10\n8,11.5,2.5,10\n9,11.5,9.5,10\n10,1.5,9.5,10\n"),
    ("pts.dat", "7,KZ,1.5,2.5,10\n8,KZ,11.5,2.5,10\n9,KZ,11.5,9.5,10\n10,KZ,1.5,9.5,10\n"),
])
def test_same_file_gives_same_coordinates_as_point_import(tmp_path, name, text):
    path = _write(tmp_path, name, text)
    points, _, _ = DataImporter.import_file(str(path))
    result = read_boundary_points(path)
    assert result.points == [(p.x, p.y) for p in points]
    assert result.ids == [p.id for p in points]


def test_excel_with_title_row_and_numeric_ids(tmp_path):
    path = tmp_path / "b.xlsx"
    frame = pd.DataFrame(
        [["三号地块边界点成果表", None, None, None], ["点号", "X", "Y", "备注"]]
        + [[index + 1, x, y, "界桩"] for index, (x, y) in enumerate(SQUARE)]
    )
    frame.to_excel(path, header=False, index=False)
    result = read_boundary_points(path)
    assert result.points == SQUARE and result.ids == ["1", "2", "3", "4"]


def test_closing_point_and_repeats_are_dropped_and_bad_rows_reported(tmp_path):
    rows = [(index + 1, x, y, 5.0) for index, (x, y) in enumerate(SQUARE)]
    rows.insert(2, (9, SQUARE[1][0], SQUARE[1][1], 5.0))   # 同一点连测两次
    rows.insert(3, (10, "", SQUARE[2][1], 5.0))            # 缺 X
    rows.append((11, SQUARE[0][0], SQUARE[0][1], 5.0))     # 回到起点闭合
    result = read_boundary_points(_write(tmp_path, "b.csv", _lines(rows)))
    assert result.points == SQUARE
    assert len(result.skipped) == 1 and "第 4 行" in result.skipped[0]


def test_too_few_points_and_no_numbers_raise_clear_errors(tmp_path):
    with pytest.raises(ValueError, match="至少需要 3 个点"):
        read_boundary_points(_write(tmp_path, "two.csv", "1,2\n3,4\n"))
    with pytest.raises(ValueError, match="没有找到坐标数据"):
        read_boundary_points(_write(tmp_path, "words.txt", "这是一份说明\n没有坐标\n"))


# ---------- 按点号连线 ----------

def _points(ids):
    return [SurveyPoint(str(point_id), float(index), float(index * 2), 10.0) for index, point_id in enumerate(ids)]


def _ids(text, points):
    return [point.id for point in parse_point_sequence(text, points)]


def test_sequence_lists_ranges_and_separators():
    points = _points(range(1, 31))
    assert _ids("5,6,7", points) == ["5", "6", "7"]
    assert _ids("5，6、7；12-15 20", points) == ["5", "6", "7", "12", "13", "14", "15", "20"]
    assert _ids("18-15,3~5", points) == ["18", "17", "16", "15", "3", "4", "5"]
    assert _ids("1-4,1", points) == ["1", "2", "3", "4"]        # 末尾回到起点 = 闭合，不重复


def test_sequence_with_prefixed_ids_padding_and_hyphens():
    points = _points(["B1", "B2", "B3", "B10", "K01", "K02", "K03", "BJ-1", "BJ-2", "BJ-3", "角点"])
    assert _ids("B1-B3", points) == ["B1", "B2", "B3"]
    assert _ids("B1-3,B10", points) == ["B1", "B2", "B3", "B10"]      # 区间终点可以省略前缀
    assert _ids("K01-K03", points) == ["K01", "K02", "K03"]            # 补零的点号
    assert _ids("BJ-1-BJ-3", points) == ["BJ-1", "BJ-2", "BJ-3"]      # 点号本身带连字符
    assert _ids("BJ-1,BJ-3,角点", points) == ["BJ-1", "BJ-3", "角点"]
    assert _ids("b1, b2, k01", points) == ["B1", "B2", "K01"]          # 大小写不敏感


def test_sequence_wildcards_use_natural_order():
    points = _points(["B10", "B2", "B1", "P1", "P2", "B3"])
    assert _ids("B*", points) == ["B1", "B2", "B3", "B10"]
    assert _ids("P?,B1", points) == ["P1", "P2", "B1"]


def test_sequence_matches_excel_style_ids():
    points = _points(["1.0", "2.0", "3.0", "4.0"])
    assert _ids("1-3", points) == ["1.0", "2.0", "3.0"]


@pytest.mark.parametrize("text, message", [
    ("", "请输入"),
    ("1,2,99", "没有点号 99"),
    ("1-3,5-8", "没有点号 6、7"),
    ("X*", "没有点号符合"),
    ("1,2", "至少需要 3 个点"),
    ("1,2,3,2,4", "出现了两次"),
    ("1-20000", "太大"),
])
def test_sequence_errors_are_specific(text, message):
    points = _points([1, 2, 3, 4, 5, 8])
    with pytest.raises(ValueError, match=message):
        parse_point_sequence(text, points)


def test_duplicate_point_ids_are_refused():
    points = _points(["1", "2", "2", "3", "4"])
    with pytest.raises(ValueError, match="有 2 个"):
        parse_point_sequence("1,2,3", points)
    assert _ids("1,3,4", points) == ["1", "3", "4"]   # 不涉及重复点号时照常
