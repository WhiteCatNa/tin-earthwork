"""
数据处理模块：导入、检查、导出
"""
import pandas as pd
import numpy as np
from typing import List, Tuple, Dict, Any, Optional
from pathlib import Path
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from core.calculator import SurveyPoint, Triangle, CalculationResult
from utils.dxf_io import read_text_with_encodings
import json
from datetime import datetime


class DataValidator:
    """数据验证器"""
    
    @staticmethod
    def validate_points(points: List[SurveyPoint]) -> Dict[str, Any]:
        """验证测量点数据"""
        issues = {
            'missing_values': [],
            'duplicate_coords': [],
            'duplicate_ids': [],
            'coord_outliers': [],
            'elevation_outliers': [],
            'format_errors': []
        }
        
        if not points:
            issues['format_errors'].append("没有数据")
            return issues
            
        coords_seen = {}
        ids_seen = {}
        elevations = [p.z for p in points]
        xs = [p.x for p in points]
        ys = [p.y for p in points]
        
        # 计算统计量用于异常值检测
        elev_mean, elev_std = np.mean(elevations), np.std(elevations)
        x_mean, x_std = np.mean(xs), np.std(xs)
        y_mean, y_std = np.mean(ys), np.std(ys)
        
        for i, pt in enumerate(points):
            # 缺失值检查
            if pd.isna(pt.x) or pd.isna(pt.y) or pd.isna(pt.z):
                issues['missing_values'].append(f"第{i+1}行: 坐标或高程缺失")
                
            # 重复坐标检查
            coord_key = (round(pt.x, 3), round(pt.y, 3))
            if coord_key in coords_seen:
                issues['duplicate_coords'].append(
                    f"第{i+1}行与第{coords_seen[coord_key]+1}行坐标重复: ({pt.x}, {pt.y})"
                )
            else:
                coords_seen[coord_key] = i
                
            # 重复点号检查
            if pt.id in ids_seen:
                issues['duplicate_ids'].append(f"第{i+1}行点号重复: {pt.id}")
            else:
                ids_seen[pt.id] = i
                
            # 坐标异常值检查 (3σ原则)
            if abs(pt.x - x_mean) > 3 * x_std and x_std > 0:
                issues['coord_outliers'].append(f"第{i+1}行 X坐标异常: {pt.x}")
            if abs(pt.y - y_mean) > 3 * y_std and y_std > 0:
                issues['coord_outliers'].append(f"第{i+1}行 Y坐标异常: {pt.y}")
                
            # 高程异常值检查
            if abs(pt.z - elev_mean) > 3 * elev_std and elev_std > 0:
                issues['elevation_outliers'].append(f"第{i+1}行 高程异常: {pt.z}")
                
        return issues
    
    @staticmethod
    def auto_detect_columns(df: pd.DataFrame) -> Dict[str, str]:
        """自动识别列映射"""
        cols = {c.lower().strip(): c for c in df.columns}
        mapping = {}
        
        # 点号列
        for key in ['点号', 'id', 'pid', 'point', '编号', 'name']:
            if key in cols:
                mapping['id'] = cols[key]
                break
                
        # X坐标列
        for key in ['x', 'x坐标', '横坐标', '东向', 'easting', '经度', 'lon', 'longitude']:
            if key in cols:
                mapping['x'] = cols[key]
                break
                
        # Y坐标列
        for key in ['y', 'y坐标', '纵坐标', '北向', 'northing', '纬度', 'lat', 'latitude']:
            if key in cols:
                mapping['y'] = cols[key]
                break
                
        # 高程列
        for key in ['z', '实测高程', '高程', 'elevation', 'alt', 'altitude', 'h']:
            if key in cols:
                mapping['z'] = cols[key]
                break

        for key in ['设计高程', '设计标高', 'design_z', 'designz', 'designelev', 'design_elev']:
            if key in cols:
                mapping['design_z'] = cols[key]
                break
                
        return mapping


class DataImporter:
    """数据导入器"""

    @staticmethod
    def complete_column_mapping(df: pd.DataFrame, mapping: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """补齐缺失的 id/x/y/z 映射，不覆盖已经识别出的列。"""
        completed = dict(mapping or {})
        cols = df.columns.tolist()
        defaults = {"id": 0, "x": 1, "y": 2, "z": 3}
        for key, idx in defaults.items():
            if key not in completed and idx < len(cols):
                completed[key] = cols[idx]
        return completed

    @staticmethod
    def points_from_dataframe(
        df: pd.DataFrame, mapping: Dict[str, str]
    ) -> Tuple[List[SurveyPoint], List[str]]:
        """按列映射解析测点；无法解析的行记入 format_errors，不静默丢弃。"""
        points: List[SurveyPoint] = []
        format_errors: List[str] = []
        id_column = mapping.get("id")
        x_column = mapping.get("x")
        y_column = mapping.get("y")
        z_column = mapping.get("z")
        design_column = mapping.get("design_z")

        for row_number, (index, row) in enumerate(df.iterrows(), start=1):
            try:
                point_id = str(row[id_column]) if id_column is not None else f"P{index + 1}"
                design_z = 0.0
                has_design_z = False
                if design_column is not None:
                    raw_design = row[design_column]
                    if pd.notna(raw_design) and str(raw_design).strip() != "":
                        design_z = float(raw_design)
                        has_design_z = True
                z_value = float(row[z_column]) if z_column is not None else 0.0
                pt = SurveyPoint(
                    id=point_id,
                    x=float(row[x_column]) if x_column is not None else 0.0,
                    y=float(row[y_column]) if y_column is not None else 0.0,
                    z=z_value,
                    design_z=design_z,
                    delta_z=(z_value - design_z) if has_design_z else 0.0,
                    has_design_z=has_design_z,
                )
                points.append(pt)
            except (ValueError, TypeError, KeyError):
                format_errors.append(f"第{row_number}行: 坐标或高程无法解析")
        return points, format_errors
    
    @staticmethod
    def import_file(filepath: str) -> Tuple[List[SurveyPoint], Dict[str, Any], pd.DataFrame]:
        """导入文件，返回(点列表, 验证结果, 原始DataFrame)"""
        path = Path(filepath)
        suffix = path.suffix.lower()
        
        try:
            if suffix in ['.xlsx', '.xls']:
                df = pd.read_excel(filepath)
            elif suffix == '.csv':
                # 尝试多种编码
                for enc in ['utf-8', 'gbk', 'gb2312', 'utf-16']:
                    try:
                        df = pd.read_csv(filepath, encoding=enc)
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    raise ValueError("无法识别CSV编码")
            elif suffix in ['.txt', '.dat']:
                points, format_errors, df = DataImporter.import_survey_text(
                    filepath, cass=(suffix == '.dat')
                )
                issues = DataValidator.validate_points(points)
                issues["format_errors"].extend(format_errors)
                return points, issues, df
            else:
                raise ValueError(f"不支持的文件格式: {suffix}")
                
        except Exception as e:
            raise ValueError(f"文件读取失败: {e}")
            
        if df.empty:
            raise ValueError("文件为空")
            
        mapping = DataImporter.complete_column_mapping(
            df, DataValidator.auto_detect_columns(df)
        )
        points, format_errors = DataImporter.points_from_dataframe(df, mapping)
        issues = DataValidator.validate_points(points)
        issues["format_errors"].extend(format_errors)
        return points, issues, df

    @staticmethod
    def _split_survey_fields(line: str) -> List[str]:
        if "," in line:
            return [part.strip() for part in line.split(",")]
        return line.split()

    @staticmethod
    def _is_survey_header(fields: List[str]) -> bool:
        joined = "".join(fields).lower()
        if any(keyword in joined for keyword in ("点号", "点名", "编号", "实测", "高程", "坐标", "northing", "easting")):
            return True
        if len(fields) >= 5:
            try:
                float(fields[2])
                float(fields[3])
                float(fields[4])
                return False
            except ValueError:
                return True
        if len(fields) >= 4:
            try:
                float(fields[1])
                float(fields[2])
                float(fields[-1])
                return False
            except ValueError:
                return True
        if len(fields) == 3:
            try:
                float(fields[0])
                float(fields[1])
                float(fields[2])
                return False
            except ValueError:
                return True
        return False

    @staticmethod
    def _looks_like_cass(fields: List[str]) -> bool:
        if len(fields) < 5:
            return False
        try:
            float(fields[2])
            float(fields[3])
            float(fields[4])
        except (TypeError, ValueError):
            return False
        try:
            float(fields[1])
            return False
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _points_to_dataframe(points: List[SurveyPoint]) -> pd.DataFrame:
        rows = {
            "点号": [point.id for point in points],
            "X坐标": [point.x for point in points],
            "Y坐标": [point.y for point in points],
            "实测高程": [point.z for point in points],
        }
        if any(point.has_design_z for point in points):
            rows["设计高程"] = [point.design_z for point in points]
        return pd.DataFrame(rows)

    @staticmethod
    def import_survey_text(
        filepath: str, cass: bool = False
    ) -> Tuple[List[SurveyPoint], List[str], pd.DataFrame]:
        """导入 CASS .dat 或点号/X/Y/Z 的空格、逗号分隔文本。"""
        text = read_text_with_encodings(filepath)
        lines = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", "*", "//")):
                continue
            if line.upper() in {"BEGIN", "END"}:
                continue
            lines.append(line)
        if not lines:
            raise ValueError("文件为空")

        first_fields = DataImporter._split_survey_fields(lines[0])
        header = DataImporter._is_survey_header(first_fields)
        body = lines[1:] if header else lines
        if not body:
            raise ValueError("文件为空")
        sample = DataImporter._split_survey_fields(body[0])
        use_cass = cass or DataImporter._looks_like_cass(sample)

        if header and not use_cass:
            rows = [DataImporter._split_survey_fields(line) for line in body]
            width = max(len(first_fields), max((len(row) for row in rows), default=0))
            columns = list(first_fields) + [f"col{i}" for i in range(len(first_fields), width)]
            padded = [row + [""] * (width - len(row)) for row in rows]
            df = pd.DataFrame(padded, columns=columns[:width])
            mapping = DataImporter.complete_column_mapping(df, DataValidator.auto_detect_columns(df))
            points, format_errors = DataImporter.points_from_dataframe(df, mapping)
            return points, format_errors, df

        points: List[SurveyPoint] = []
        format_errors: List[str] = []
        for row_number, line in enumerate(body, start=1):
            fields = DataImporter._split_survey_fields(line)
            try:
                if use_cass:
                    if len(fields) >= 5:
                        point_id, easting, northing, height = fields[0], fields[2], fields[3], fields[4]
                    elif len(fields) == 4:
                        point_id, easting, northing, height = fields[0], fields[1], fields[2], fields[3]
                    elif len(fields) == 3:
                        point_id, easting, northing, height = f"P{row_number}", fields[0], fields[1], fields[2]
                    else:
                        raise ValueError("字段不足")
                    point = SurveyPoint(
                        id=str(point_id),
                        x=float(easting),
                        y=float(northing),
                        z=float(height),
                    )
                else:
                    if len(fields) >= 4:
                        point = SurveyPoint(
                            id=str(fields[0]),
                            x=float(fields[1]),
                            y=float(fields[2]),
                            z=float(fields[3]),
                        )
                    elif len(fields) == 3:
                        point = SurveyPoint(
                            id=f"P{row_number}",
                            x=float(fields[0]),
                            y=float(fields[1]),
                            z=float(fields[2]),
                        )
                    else:
                        raise ValueError("字段不足")
                points.append(point)
            except (ValueError, TypeError):
                format_errors.append(f"第{row_number}行: 坐标或高程无法解析")
        if not points:
            raise ValueError("没有可解析的测量点")
        return points, format_errors, DataImporter._points_to_dataframe(points)


class DataExporter:
    """数据导出器"""
    
    def __init__(self, project_name: str = "土方计算项目"):
        self.project_name = project_name
        self.calc_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
    def export_summary_excel(self, result: CalculationResult, 
                             design_elevation: float, 
                             filepath: str) -> bool:
        """导出汇总表"""
        wb = openpyxl.Workbook()
        
        # 样式定义
        header_font = Font(bold=True, size=11)
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_font_white = Font(bold=True, size=11, color="FFFFFF")
        title_font = Font(bold=True, size=14)
        subtitle_font = Font(bold=True, size=11)
        thin_border = Border(
            left=Side(style='thin'), right=Side(style='thin'),
            top=Side(style='thin'), bottom=Side(style='thin')
        )
        center_align = Alignment(horizontal='center', vertical='center')
        
        # ===== Sheet 1: 汇总表 =====
        ws1 = wb.active
        ws1.title = "土方量汇总表"
        
        # 标题区
        ws1.merge_cells('A1:F1')
        ws1['A1'] = self.project_name
        ws1['A1'].font = title_font
        ws1['A1'].alignment = center_align
        
        ws1.merge_cells('A2:F2')
        ws1['A2'] = "土方量计算汇总表"
        ws1['A2'].font = subtitle_font
        ws1['A2'].alignment = center_align
        
        # 项目信息
        info_row = 4
        ws1[f'A{info_row}'] = "计算日期"
        ws1[f'B{info_row}'] = self.calc_date
        ws1[f'D{info_row}'] = "设计高程"
        ws1[f'E{info_row}'] = design_elevation
        
        ws1[f'A{info_row+1}'] = "计算方法"
        ws1[f'B{info_row+1}'] = "TIN三角网法 (Delaunay剖分)"
        ws1[f'D{info_row+1}'] = "三角形总数"
        ws1[f'E{info_row+1}'] = result.triangle_count
        
        ws1[f'A{info_row+2}'] = "混合三角形数"
        ws1[f'B{info_row+2}'] = result.mixed_triangle_count
        ws1[f'D{info_row+2}'] = "边界点数"
        ws1[f'E{info_row+2}'] = len(result.boundary_points) if result.boundary_points else 0
        
        # 汇总结果表头
        header_row = info_row + 4
        headers = ['项目', '数值', '单位', '说明']
        for col, h in enumerate(headers, 1):
            cell = ws1.cell(row=header_row, column=col, value=h)
            cell.font = header_font_white
            cell.fill = header_fill
            cell.alignment = center_align
            cell.border = thin_border
            
        summary_data = [
            ['总挖方量', round(result.total_cut, 3), 'm³', '实测高程高于设计高程部分'],
            ['总填方量', round(result.total_fill, 3), 'm³', '实测高程低于设计高程部分'],
            ['挖填差值', round(result.net_volume, 3), 'm³', '正值为挖方大，负值为填方大'],
        ]
        
        for i, row_data in enumerate(summary_data):
            for col, val in enumerate(row_data, 1):
                cell = ws1.cell(row=header_row + 1 + i, column=col, value=val)
                cell.border = thin_border
                cell.alignment = center_align
                
        # 设置列宽
        ws1.column_dimensions['A'].width = 18
        ws1.column_dimensions['B'].width = 18
        ws1.column_dimensions['C'].width = 10
        ws1.column_dimensions['D'].width = 35
        ws1.column_dimensions['E'].width = 18
        ws1.column_dimensions['F'].width = 18
        
        # ===== Sheet 2: 三角形计算明细 =====
        ws2 = wb.create_sheet("三角形计算明细")
        
        detail_headers = [
            '三角形编号', '顶点1点号', '顶点1X', '顶点1Y', '顶点1实测高程', '顶点1设计高程', '顶点1高差',
            '顶点2点号', '顶点2X', '顶点2Y', '顶点2实测高程', '顶点2设计高程', '顶点2高差',
            '顶点3点号', '顶点3X', '顶点3Y', '顶点3实测高程', '顶点3设计高程', '顶点3高差',
            '水平投影面积(m²)', '平均高差(m)', '挖方量(m³)', '填方量(m³)', '净体积(m³)', '是否混合', '是否边界外'
        ]
        
        for col, h in enumerate(detail_headers, 1):
            cell = ws2.cell(row=1, column=col, value=h)
            cell.font = header_font_white
            cell.fill = header_fill
            cell.alignment = center_align
            cell.border = thin_border
            
        row_idx = 2
        for tri in result.triangles:
            if tri.is_boundary:
                continue

            p0, p1, p2 = tri.vertex_points
            row_data = [
                tri.id,
                p0.id, p0.x, p0.y, p0.z, p0.design_z, p0.delta_z,
                p1.id, p1.x, p1.y, p1.z, p1.design_z, p1.delta_z,
                p2.id, p2.x, p2.y, p2.z, p2.design_z, p2.delta_z,
                round(tri.area, 3),
                round(tri.avg_delta_z, 3) if not tri.is_mixed else '',
                round(tri.cut_volume, 3),
                round(tri.fill_volume, 3),
                round(tri.volume, 3),
                '是' if tri.is_mixed else '否',
                '是' if tri.is_boundary else '否'
            ]
            
            for col, val in enumerate(row_data, 1):
                cell = ws2.cell(row=row_idx, column=col, value=val)
                cell.border = thin_border
                cell.alignment = center_align
            row_idx += 1

        # 调整列宽
        for col in range(1, len(detail_headers) + 1):
            ws2.column_dimensions[get_column_letter(col)].width = 14
            
        # ===== Sheet 3: 异常数据检查记录 =====
        ws3 = wb.create_sheet("异常数据检查")
        ws3.append(["检查项目", "发现数量", "详细信息"])
        for cell in ws3[1]:
            cell.font = header_font_white
            cell.fill = header_fill
            cell.alignment = center_align
            cell.border = thin_border
            
        # 这里可以添加验证时发现的问题
        ws3.append(["数据完整性", "已在导入时检查", "见导入日志"])
        ws3.append(["坐标重复", "已在导入时检查", "见导入日志"])
        ws3.append(["高程异常", "已在导入时检查", "见导入日志"])
        
        for row in ws3.iter_rows(min_row=2, max_row=4, min_col=1, max_col=3):
            for cell in row:
                cell.border = thin_border
                cell.alignment = center_align
                
        # 保存
        try:
            wb.save(filepath)
            return True
        except Exception as e:
            print(f"导出失败: {e}")
            return False
    
    def export_triangles_csv(self, result: CalculationResult, filepath: str) -> bool:
        """导出三角形明细CSV"""
        try:
            rows = []
            for tri in result.triangles:
                if tri.is_boundary:
                    continue
                p0, p1, p2 = tri.vertex_points
                rows.append({
                    '三角形编号': tri.id,
                    '顶点1': p0.id, 'X1': p0.x, 'Y1': p0.y, 'Z1': p0.z, '设计高程1': p0.design_z, '高差1': p0.delta_z,
                    '顶点2': p1.id, 'X2': p1.x, 'Y2': p1.y, 'Z2': p1.z, '设计高程2': p1.design_z, '高差2': p1.delta_z,
                    '顶点3': p2.id, 'X3': p2.x, 'Y3': p2.y, 'Z3': p2.z, '设计高程3': p2.design_z, '高差3': p2.delta_z,
                    '面积': round(tri.area, 3),
                    '平均高差': round(tri.avg_delta_z, 3) if not tri.is_mixed else '',
                    '挖方量': round(tri.cut_volume, 3),
                    '填方量': round(tri.fill_volume, 3),
                    '净体积': round(tri.volume, 3),
                    '是否混合': '是' if tri.is_mixed else '否',
                    '是否边界外': '是' if tri.is_boundary else '否'
                })
            df = pd.DataFrame(rows)
            df.to_csv(filepath, index=False, encoding='utf-8-sig')
            return True
        except Exception as e:
            print(f"CSV导出失败: {e}")
            return False
    
    def export_boundary_dxf(self, result: CalculationResult, filepath: str) -> bool:
        """导出计算边界（闭合多段线）和零填挖线到 DXF。"""
        from core.calculator import chain_segments, extract_zero_contour_segments

        try:
            with open(filepath, "w", encoding="utf-8") as handle:
                handle.write("0\nSECTION\n2\nHEADER\n0\nENDSEC\n")
                handle.write("0\nSECTION\n2\nENTITIES\n")
                if result.boundary_points:
                    self._write_dxf_lwpolyline(
                        handle, "BOUNDARY", result.boundary_points, closed=True
                    )
                for polyline in chain_segments(extract_zero_contour_segments(result)):
                    if len(polyline) >= 2:
                        self._write_dxf_lwpolyline(handle, "ZERO_CONTOUR", polyline, closed=False)
                handle.write("0\nENDSEC\n0\nEOF\n")
            return True
        except Exception as error:
            print(f"DXF导出失败: {error}")
            return False

    @staticmethod
    def _write_dxf_lwpolyline(handle, layer: str, points, closed: bool) -> None:
        coords = [(float(x), float(y)) for x, y in points]
        if closed and coords and coords[0] != coords[-1]:
            vertex_count = len(coords)
        else:
            vertex_count = len(coords)
        handle.write("0\nLWPOLYLINE\n")
        handle.write(f"8\n{layer}\n")
        handle.write(f"90\n{vertex_count}\n")
        handle.write(f"70\n{1 if closed else 0}\n")
        for x, y in coords:
            handle.write(f"10\n{x}\n20\n{y}\n")
    
    def generate_report_text(self, result: CalculationResult, 
                             design_elevation: float,
                             point_count: int) -> str:
        """生成文本报告"""
        report = f"""
============================================================
              {self.project_name} - 土方计算报告
============================================================

【项目基本信息】
计算日期: {self.calc_date}
计算方法: TIN三角网法 (Delaunay三角剖分)
测量点数: {point_count}
三角形总数: {result.triangle_count}
混合挖填三角形: {result.mixed_triangle_count}
计算边界点数: {len(result.boundary_points) if result.boundary_points else 0}
设计高程: {design_elevation} m

【计算结果汇总】
总挖方量: {result.total_cut:.3f} m³
总填方量: {result.total_fill:.3f} m³
挖填差值: {result.net_volume:.3f} m³
{'(挖方大于填方，需外运)' if result.net_volume > 0 else '(填方大于挖方，需借方)' if result.net_volume < 0 else '(挖填平衡)'}

【说明】
1. 本成果基于实测点构建TIN三角网，按水平投影面积×平均高差法计算。
2. 混合挖填三角形已按零填挖线自动分割计算，避免正负高差抵消。
3. 所有三角形均保留面积、顶点高程、高差及分项体积，可逐项复核。
4. 计算结果仅供工程参考，正式计量请以复核成果为准。

============================================================
"""
        return report

    def export_pdf_report(
        self,
        result: CalculationResult,
        design_elevation: float,
        point_count: int,
        points: List[SurveyPoint],
        boundary: List[Tuple[float, float]],
        filepath: str,
    ) -> bool:
        """导出含挖填汇总与成果图的 PDF 计算书。"""
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_pdf import PdfPages
        from matplotlib.figure import Figure
        from utils.plotter import create_standalone_figure, setup_chinese_font

        setup_chinese_font()
        try:
            with PdfPages(filepath) as pdf:
                summary = Figure(figsize=(8.27, 11.69))
                axis = summary.add_subplot(111)
                axis.axis("off")
                axis.text(
                    0.08,
                    0.96,
                    self.generate_report_text(result, design_elevation, point_count).strip(),
                    va="top",
                    ha="left",
                    fontsize=10,
                    family=plt.rcParams["font.family"],
                    wrap=True,
                    transform=axis.transAxes,
                )
                pdf.savefig(summary)
                plt.close(summary)

                figure = create_standalone_figure(
                    result, points, boundary, design_elevation, self.project_name
                )
                figure.suptitle(f"{self.project_name} 计算结果图", fontsize=14)
                pdf.savefig(figure)
                plt.close(figure)
            return True
        except Exception as error:
            print(f"PDF导出失败: {error}")
            return False


def save_project(
    filepath: str,
    points: List[SurveyPoint],
    boundary: List[Tuple[float, float]],
    design_elevation: float = 0.0,
    use_partition: bool = False,
    partition: Optional[Dict[str, float]] = None,
    project_name: str = "TIN土方计算项目",
) -> None:
    """保存可再次打开的工程文件（测点、边界、设计高程）。"""
    payload = {
        "format": "tin-earthwork-project",
        "format_version": 1,
        "app_version": "1.3.0",
        "project_name": project_name,
        "design_elevation": float(design_elevation),
        "use_partition": bool(use_partition),
        "partition": {str(key): float(value) for key, value in (partition or {}).items()},
        "boundary": [[float(x), float(y)] for x, y in boundary],
        "points": [
            {
                "id": point.id,
                "x": point.x,
                "y": point.y,
                "z": point.z,
                "design_z": point.design_z,
                "has_design_z": bool(point.has_design_z),
            }
            for point in points
        ],
    }
    with open(filepath, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def load_project(filepath: str) -> Dict[str, Any]:
    """读取工程文件，返回测点对象和计算设置。"""
    with open(filepath, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("format") not in (None, "tin-earthwork-project"):
        raise ValueError("不是 TIN 土方工程文件")
    points = []
    for row in payload.get("points") or []:
        design_z = float(row.get("design_z", 0.0))
        has_design_z = bool(row.get("has_design_z", "design_z" in row))
        z_value = float(row["z"])
        points.append(
            SurveyPoint(
                id=str(row["id"]),
                x=float(row["x"]),
                y=float(row["y"]),
                z=z_value,
                design_z=design_z,
                delta_z=z_value - design_z if has_design_z else 0.0,
                has_design_z=has_design_z,
            )
        )
    boundary = [(float(x), float(y)) for x, y in (payload.get("boundary") or [])]
    partition = {str(key): float(value) for key, value in (payload.get("partition") or {}).items()}
    return {
        "project_name": payload.get("project_name") or "TIN土方计算项目",
        "design_elevation": float(payload.get("design_elevation") or 0.0),
        "use_partition": bool(payload.get("use_partition")),
        "partition": partition,
        "boundary": boundary,
        "points": points,
    }


def save_project_config(config: Dict[str, Any], filepath: str):
    """保存项目配置"""
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def load_project_config(filepath: str) -> Dict[str, Any]:
    """加载项目配置"""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)