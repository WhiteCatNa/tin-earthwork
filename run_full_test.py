#!/usr/bin/env python3
"""
自动化测试脚本：完整跑通数据导入 -> 边界设置 -> 计算 -> 导出全流程。
无需 GUI 交互，直接在命令行运行。
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
from openpyxl import load_workbook

from core.calculator import TINEarthworkCalculator, SurveyPoint
from core.grid_check import run_grid_check
from core.surface import PlaneDesign
from utils.data_handler import DataImporter, DataExporter


def _require(condition, message):
    if not condition:
        raise RuntimeError(message)


def generate_test_data(seed=42):
    """生成 1000 条确定性测试数据。"""
    rng = np.random.default_rng(seed)
    points = []
    for i in range(25):
        for j in range(20):
            x = i * 8 + rng.uniform(-1, 1)
            y = j * 7.5 + rng.uniform(-1, 1)
            terrain = 0.5 * np.sin(x / 20) * np.cos(y / 15)
            noise = rng.normal(0, 0.3)
            points.append(SurveyPoint(f"G{len(points) + 1}", round(x, 3), round(y, 3), round(50 + terrain + noise, 3)))

    for _ in range(500):
        x = rng.uniform(0, 200)
        y = rng.uniform(0, 150)
        terrain = 0.5 * np.sin(x / 20) * np.cos(y / 15)
        noise = rng.normal(0, 0.3)
        points.append(SurveyPoint(f"R{len(points) + 1}", round(x, 3), round(y, 3), round(50 + terrain + noise, 3)))
    return points


def save_test_excel(points, filepath):
    """保存为 Excel 文件。"""
    pd.DataFrame([{"点号": p.id, "X坐标": p.x, "Y坐标": p.y, "实测高程": p.z} for p in points]).to_excel(filepath, index=False)
    return filepath


def run_full_pipeline(output_dir="."):
    """运行完整流程并将所有产物写入 output_dir。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    print("=" * 60)
    print("TIN 土方自动算量系统 - 自动化全流程测试")
    print("=" * 60)

    points = generate_test_data()
    excel_path = save_test_excel(points, output_dir / "test_data_1000.xlsx")
    print(f"\n[1/9] 生成测试数据: {excel_path} ({len(points)} 点)")

    imported_points, issues, _ = DataImporter.import_file(excel_path)
    _require(len(imported_points) == len(points), "导入点数与生成点数不一致")
    blocking_issues = {
        name: values for name, values in issues.items()
        if name not in {"coord_outliers", "elevation_outliers"} and values
    }
    _require(not blocking_issues, f"导入数据存在阻断问题: {blocking_issues}")
    if issues["coord_outliers"] or issues["elevation_outliers"]:
        print("    导入质量检查提示：检测到统计离群值，继续使用确定性测试数据。")
    print(f"[2/9] 导入并验证通过: {len(imported_points)} 点")

    boundary = [(0, 0), (200, 0), (200, 150), (0, 150)]
    print(f"[3/9] 设置矩形边界: {len(boundary)} 点")

    design_elevation = 50.0
    calc = TINEarthworkCalculator(design_elevation)
    calc.add_points(imported_points)
    calc.set_boundary(boundary)
    result = calc.run_full_calculation()
    _require(result.triangle_count > 0, "边界内没有可计算三角形")
    _require(abs(result.computed_area - 200 * 150) < 1e-6 or result.warnings, "计算面积与边界面积不符且没有提示")
    print(f"[4/9] 计算通过: {result.triangle_count} 个三角形，计算面积 {result.computed_area:.1f} m²")
    for warning in result.warnings:
        print(f"    提示: {warning}")

    exporter = DataExporter("TIN土方计算项目 - 自动化测试")
    report_path = output_dir / "计算报告_自动化测试.xlsx"
    csv_path = output_dir / "计算明细_自动化测试.csv"
    txt_path = output_dir / "计算报告_自动化测试.txt"
    _require(exporter.export_summary_excel(result, report_path, imported_points), "Excel 报告导出失败")
    _require(exporter.export_triangles_csv(result, csv_path), "CSV 明细导出失败")
    txt_path.write_text(exporter.generate_report_text(result, len(imported_points)), encoding="utf-8")
    _require(report_path.is_file() and report_path.stat().st_size > 0, "Excel 报告为空")
    _require(csv_path.is_file() and csv_path.stat().st_size > 0, "CSV 明细为空")
    _require(txt_path.is_file() and txt_path.stat().st_size > 0, "文本报告为空")
    _require(load_workbook(report_path).sheetnames == ["土方量汇总表", "三角形计算明细", "异常数据检查"], "Excel 工作表不完整")
    _require(not pd.read_csv(csv_path, encoding="utf-8-sig").empty, "CSV 明细没有数据")
    print(f"[5/9] Excel/CSV/文本报告导出通过")

    # 斜面设计面：地形起伏很小时，设计面抬高 0.1 m 的斜面净方量应接近 −面积×(平均高差)
    plane = PlaneDesign(x0=100.0, y0=75.0, h0=50.0, slope_x=0.5, slope_y=-0.3)
    calc.set_design_plane(plane)
    plane_result = calc.run_full_calculation()
    _require(plane_result.total_cut > 0 and plane_result.total_fill > 0, "斜面设计面计算结果异常")
    _require("斜面" in plane_result.design_text, "斜面设计面说明缺失")
    print(f"[6/9] 斜面设计面计算通过: 挖方 {plane_result.total_cut:.1f} m³，填方 {plane_result.total_fill:.1f} m³")

    # 两期对比：后期在前期基础上整体下挖约 0.5 m，测点位置不同
    after = generate_after_data()
    calc.set_design_elevation(design_elevation)
    calc.set_compare_points(after)
    compare = calc.run_full_calculation()
    _require(compare.is_compare, "两期对比未生效")
    _require(compare.total_cut > compare.total_fill * 10, "两期对比：整体下挖时挖方应远大于填方")
    mean_cut = compare.net_volume / compare.computed_area
    _require(0.3 < mean_cut < 0.7, f"两期对比平均下挖深度 {mean_cut:.3f} m 不在预期范围")
    print(f"[7/9] 两期对比通过: 后期 {len(after)} 点，挖方 {compare.total_cut:.1f} m³，平均下挖 {mean_cut:.3f} m")

    # 方格网校核并导出全部格式
    compare.grid_check = run_grid_check(calc, compare, 10.0)
    grid = compare.grid_check
    relative = abs(grid.net_volume - compare.net_volume) / abs(compare.net_volume)
    _require(relative < 0.05, f"方格网与 TIN 法净方量相差 {relative:.1%}，超出预期")
    exporter = DataExporter("TIN土方计算项目 - 两期对比")
    compare_excel = output_dir / "两期对比_方格网校核.xlsx"
    compare_dxf = output_dir / "两期对比_方格网.dxf"
    compare_pdf = output_dir / "两期对比_计算书.pdf"
    exporter.export_summary_excel(compare, compare_excel, imported_points)
    exporter.export_boundary_dxf(compare, compare_dxf)
    exporter.export_pdf_report(compare, len(imported_points), imported_points, boundary, compare_pdf)
    sheets = load_workbook(compare_excel).sheetnames
    _require(sheets[-3:] == ["方格网校核", "方格网角点", "方格网方格"], f"方格网工作表不完整: {sheets}")
    _require("GRID_HEIGHT" in compare_dxf.read_text(encoding="utf-8"), "DXF 缺少方格网标注")
    _require(compare_pdf.read_bytes().startswith(b"%PDF"), "PDF 计算书无效")
    print(f"[8/9] 方格网校核（边长 10 m，{len(grid.cells)} 格，净方量相差 {relative:.2%}）与导出通过")
    print(f"[9/9] 全流程通过，输出目录: {output_dir}")
    return result


def generate_after_data(seed=7):
    """后期测量：与前期同一地形整体下挖约 0.5 m，测点位置、数量不同。"""
    rng = np.random.default_rng(seed)
    points = []
    for x, y in [(-2, -2), (202, -2), (202, 152), (-2, 152)]:
        points.append(SurveyPoint(f"H{len(points) + 1}", x, y, 49.5 + 0.5 * np.sin(x / 20) * np.cos(y / 15)))
    for _ in range(700):
        x = rng.uniform(0, 200)
        y = rng.uniform(0, 150)
        terrain = 0.5 * np.sin(x / 20) * np.cos(y / 15)
        points.append(SurveyPoint(f"H{len(points) + 1}", round(x, 3), round(y, 3),
                                  round(49.5 + terrain + rng.normal(0, 0.1), 3)))
    return points


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="运行 TIN 土方全流程自动化测试")
    parser.add_argument("--output-dir", default=".", help="测试产物输出目录")
    args = parser.parse_args()
    run_full_pipeline(args.output_dir)
