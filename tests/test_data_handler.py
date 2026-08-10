import pandas as pd
import openpyxl

from core.calculator import SurveyPoint, TINEarthworkCalculator
from utils.data_handler import DataExporter, DataImporter, DataValidator


def _result():
    points = [
        SurveyPoint("A", 0, 0, 12),
        SurveyPoint("B", 1, 0, 8),
        SurveyPoint("C", 0, 1, 10),
    ]
    calculator = TINEarthworkCalculator(10)
    calculator.add_points(points)
    return calculator.run_full_calculation(), points


def test_import_csv_and_detect_columns(tmp_path):
    source = tmp_path / "points.csv"
    pd.DataFrame({
        "id": ["A", "B", "C"],
        "x": [0, 1, 0],
        "y": [0, 0, 1],
        "z": [10, 11, 12],
    }).to_csv(source, index=False)

    points, issues, _ = DataImporter.import_file(source)
    assert [point.id for point in points] == ["A", "B", "C"]
    assert not any(issues.values())


def test_validator_reports_duplicate_ids_and_coordinates():
    issues = DataValidator.validate_points([
        SurveyPoint("A", 0, 0, 10),
        SurveyPoint("A", 0, 0, 11),
    ])
    assert issues["duplicate_ids"]
    assert issues["duplicate_coords"]


def test_exports_are_created_and_readable(tmp_path):
    result, points = _result()
    exporter = DataExporter("测试项目")
    excel_path = tmp_path / "report.xlsx"
    csv_path = tmp_path / "detail.csv"

    assert exporter.export_summary_excel(result, 10, excel_path)
    assert exporter.export_triangles_csv(result, csv_path)
    assert openpyxl.load_workbook(excel_path).sheetnames == ["土方量汇总表", "三角形计算明细", "异常数据检查"]
    assert not pd.read_csv(csv_path, encoding="utf-8-sig").empty
    assert "总挖方量" in exporter.generate_report_text(result, 10, len(points))
