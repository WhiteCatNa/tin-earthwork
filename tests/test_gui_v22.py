"""第二批界面流程：等高线、断面法与断面图、设计三角网导入、三维视图、工程存取。"""
import pytest

from tests.test_gui_v21 import _calculate, _load, _wait, app  # noqa: F401  复用夹具


def _design_dxf(path, z=10.5):
    points = "".join(f"0\nPOINT\n8\nGCD\n10\n{x}\n20\n{y}\n30\n{z}\n"
                     for x in (0, 10, 20) for y in (0, 10, 20))
    path.write_text(f"0\nSECTION\n2\nENTITIES\n{points}0\nENDSEC\n0\nEOF\n", encoding="utf-8")
    return str(path)


def test_contours_and_sections(app):
    cf = _load(app, draw_hole=False)
    cf.design_elevation_var.set(10.0)
    cf.contour_interval_var.set("0.5")
    cf.sections_enabled_var.set(True)
    cf.section_spacing_var.set("5")
    cf.section_angle_var.set("0")
    result = _calculate(app, cf)
    assert result.contours is not None
    assert sorted({c.level for c in result.contours.natural}) == [10.0, 10.5, 11.0, 11.5, 12.0]   # 边界上 x=0、x=20 也是等高线
    assert result.sections is not None and len(result.sections.sections) == 5
    assert result.sections.total_cut == pytest.approx(result.total_cut, rel=1e-9)
    assert len(cf.section_combo["values"]) == 5
    cf._step_section(1)
    assert cf.section_combo.current() == 3
    assert cf.plotter.ax.collections   # 等高线已画在结果图上


def test_design_tin_import_and_calculate(app, tmp_path):
    cf = _load(app, draw_hole=False)
    cf.design_mode_var.set("design_tin")
    cf._on_mode_change()
    assert cf.load_design_tin_file(_design_dxf(tmp_path / "design.dxf"))
    assert len(cf.design_tin_points) == 9
    result = _calculate(app, cf)
    assert result.is_design_tin
    # 地面 10 + 0.1x 对设计面 10.5：挖 ∫(0.1x − 0.5)+ = 20·∫₅²⁰(0.1x−0.5)dx = 225
    assert result.total_cut == pytest.approx(225, abs=1e-6)
    assert result.total_fill == pytest.approx(25, abs=1e-6)

    path = tmp_path / "v22.tinproj.json"
    app.save_project_to(str(path))
    app.load_project_from(str(path))
    cf = app.calc_frame
    assert cf.design_mode_var.get() == "design_tin" and len(cf.design_tin_points) == 9
    assert _calculate(app, cf).total_cut == pytest.approx(225, abs=1e-6)


def test_three_d_view(app):
    cf = _load(app)
    cf.design_elevation_var.set(10.5)
    _calculate(app, cf)
    cf._open_3d_view()
    window = cf.view3d
    assert window is not None and window.winfo_exists()
    window.exaggeration_var.set("3")
    window.redraw()
    assert "3 倍" in window.status_var.get()
    window.show_natural_var.set(False)
    window.redraw()
    assert not window.cax.get_visible()
    window.exaggeration_var.set("x")
    window.redraw()
    assert "0.1~100" in window.status_var.get()
    window.destroy()


def test_layers_in_gui_and_project(app, tmp_path):
    cf = _load(app, draw_hole=False)
    cf.design_elevation_var.set(11.0)
    cf.layers_enabled_var.set(True)
    cf.layer_mode_var.set("depth")
    cf.layer_values_var.set("0.5")
    result = _calculate(app, cf)
    assert [row.cut for row in result.layers.rows] == pytest.approx([75, 25], abs=1e-9)
    rows = [cf.tree_summary.item(i)["values"][0] for i in cf.tree_summary.get_children()]
    assert "分层土方" in rows

    path = tmp_path / "layers.tinproj.json"
    app.save_project_to(str(path))
    app.load_project_from(str(path))
    cf = app.calc_frame
    assert cf.layers_enabled_var.get() and cf.layer_mode_var.get() == "depth"
    assert cf.layer_values_var.get() == "0.5"

    cf.layer_values_var.set("-1")
    errors = []
    import tkinter.messagebox as mb
    original = mb.showerror
    mb.showerror = lambda title, msg, **k: errors.append(msg)
    try:
        cf._run_calculation()
    finally:
        mb.showerror = original
    assert errors and "大于 0" in errors[0]


def test_import_contour_dxf_and_las(app, tmp_path, monkeypatch):
    from tests.test_terrain_import import _dxf, _lw, _write_las
    infos = []
    monkeypatch.setattr("tkinter.messagebox.showinfo", lambda title, msg, **k: infos.append(msg))
    monkeypatch.setattr("tkinter.messagebox.showwarning", lambda title, msg, **k: infos.append(msg))
    frame = app.import_frame
    path = tmp_path / "dgx.dxf"
    path.write_text(_dxf("".join(_lw("DGX", 10 + k, [(10 * k, 0), (10 * k, 20)]) for k in range(3))),
                    encoding="utf-8")
    seen = {}
    frame.ask_dxf_options = lambda parent, sources: seen.setdefault("dxf", ([True] * len(sources), 2.0))
    frame.file_var.set(str(path))
    frame._import_file()
    assert len(frame.points) == 33 and "33 个" in infos[-1]          # 每条线 11 点
    assert frame.column_mapping.get("z") == "Z"

    las = tmp_path / "c.las"
    _write_las(las, [(1000 + i, 2000 + j, 5.0) for i in range(10) for j in range(10)], [2] * 100)
    frame.ask_las_options = lambda parent, cloud: (2.0, "lowest", True)
    frame.file_var.set(str(las))
    frame._import_file()
    assert len(frame.points) == 25

    frame.ask_las_options = lambda parent, cloud: None              # 取消
    frame._import_file()
    assert len(frame.points) == 25


def test_terrain_dialogs(app, tmp_path):
    import numpy as np
    from gui.terrain_dialog import DxfTerrainDialog, LasDialog
    from utils.terrain_import import KIND_CONTOUR, KIND_POINT, LasCloud, TerrainSource
    sources = [TerrainSource("GCD", KIND_POINT, points=[(0, 0, 1)]),
               TerrainSource("DGX", KIND_CONTOUR, lines=[[(0, 0, 2), (1, 0, 2)]])]
    dialog = DxfTerrainDialog(app, sources)
    dialog.spacing_var.set("abc")
    dialog.ok()
    assert dialog.result is None
    dialog.vars[0].set(False)
    dialog.spacing_var.set("2.5")
    dialog.ok()
    assert dialog.result == ([False, True], 2.5)

    cloud = LasCloud(np.array([(0, 0, 1), (100, 100, 2)], dtype=float), np.array([1, 1]), "1.2", 1)
    dialog = LasDialog(app, cloud)
    assert not dialog.ground_var.get()
    dialog.cell_var.set("5")
    dialog.method_var.set("center")
    dialog.ok()
    assert dialog.result == (5.0, "center", False)
