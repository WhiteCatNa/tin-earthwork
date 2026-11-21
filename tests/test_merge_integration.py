"""Cross-branch regressions for constraints, engineering settings and project UX."""
import time

import pytest

from core.balance import balance_elevation, balance_plane
from core.breaklines import Breakline
from core.calculator import COMPARE_DESIGN, SurveyPoint
from core.grid_check import run_grid_check
from core.surface import PlaneDesign
from tests.test_breaklines import EXACT_CUT, SECTION, SITE, _calc, _survey, flat, ground

BOUNDARY = [(0.0, 0.0), (100.0, 0.0), (100.0, 60.0), (0.0, 60.0)]
HOLE = [(10.0, 20.0), (40.0, 20.0), (40.0, 28.0), (10.0, 28.0)]
AREA = 6000.0 - 30.0 * 8.0
# Hole spans the full ditch; its cut volume at design 5 m is 900 m³.
HOLE_CUT = (5.0 + 0.01 * 25.0) * 30.0 * 8.0 - SECTION * 30.0


def _ditch():
    points, edge_xy = _survey(with_edge_points=False)
    lines = [Breakline([(float(x), float(y), float(ground(x, y))) for x, y in line],
                       name=f"沟线{i + 1}") for i, line in enumerate(edge_xy)]
    calc = _calc(points, lines=lines)
    calc.set_holes([HOLE])
    return calc, points, lines


def test_hole_and_3d_breaklines_share_exact_volume_with_grid_check():
    calc, _, _ = _ditch()
    result = calc.run_full_calculation()
    assert result.total_cut == pytest.approx(EXACT_CUT - HOLE_CUT, abs=1e-6)
    assert result.total_fill == pytest.approx(0, abs=1e-9)
    assert result.computed_area == pytest.approx(AREA, abs=1e-6)
    grid = run_grid_check(calc, result, 1.0)
    assert grid.computed_area == pytest.approx(AREA, abs=1e-6)
    assert grid.total_cut == pytest.approx(result.total_cut, abs=1e-6)
    assert grid.total_fill == pytest.approx(0, abs=1e-6)


@pytest.mark.parametrize("mode", ["flat", "plane"])
def test_balance_preserves_3d_breaklines_and_hole_without_mutating_source(mode):
    calc, _, lines = _ditch()
    before = [(p.design_z, p.delta_z) for p in calc.points]
    if mode == "flat":
        solved = balance_elevation(calc)
        # Integral of natural terrain / remaining area.
        expected = (6000 * 10.5 - SECTION * 100 -
                    ((10 + 0.01 * 25) * 240 - SECTION * 30)) / AREA
        assert solved.elevation == pytest.approx(expected, abs=2e-5)
    else:
        solved = balance_plane(calc, PlaneDesign(50, 30, 0, slope_x=1.0))
        assert solved.plane.h0 == pytest.approx(10.5 - SECTION * 70 / AREA, abs=2e-5)
        assert solved.plane.slope_x == 1.0
    assert [(p.design_z, p.delta_z) for p in calc.points] == before
    assert calc.breaklines == lines and calc.holes == [HOLE]
    if solved.plane:
        calc.set_design_plane(solved.plane)
    else:
        calc.set_design_elevation(solved.elevation)
    result = calc.run_full_calculation()
    assert result.total_cut == pytest.approx(solved.cut, abs=1e-5)
    assert result.total_fill == pytest.approx(solved.fill, abs=1e-5)
    assert result.net_volume == pytest.approx(0, abs=0.1)


def test_design_tin_with_hole_uses_constraints_and_design_vertex_names():
    calc, after, lines = _ditch()
    calc.add_points([SurveyPoint(p.id, p.x, p.y, float(flat(p.x, p.y))) for p in after])
    calc.set_compare_points(after, kind=COMPARE_DESIGN)
    calc.set_breaklines([], lines)
    result = calc.run_full_calculation()
    assert result.is_design_tin and result.has_breaklines
    assert result.total_cut == pytest.approx(SECTION * 70, abs=1e-6)
    assert result.total_fill == pytest.approx(0, abs=1e-6)
    assert result.computed_area == pytest.approx(AREA, abs=1e-6)
    assert calc.breakline_points
    assert all(str(p.id).startswith("设计-特征线") for p in calc.breakline_points)


def _wait(app, predicate, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.update()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for calculation")


def _load_gui(app):
    _, points, lines = _ditch()
    app.import_frame.load_points(points)
    app.import_frame._confirm_points()
    app.boundary_frame.set_boundary(BOUNDARY)
    app.boundary_frame.set_holes([HOLE])
    app.boundary_frame.set_breaklines(lines)
    app.boundary_frame._confirm_boundary()
    return app.calc_frame, lines


def test_combined_project_round_trip_and_new_setting_dirty_tracking(main_window, tmp_path, monkeypatch):
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(f"tkinter.messagebox.{name}", lambda *a, **k: "ok")
    app = main_window
    cf, lines = _load_gui(app)
    cf.design_elevation_var.set(5.0)
    cf.loose_var.set("1.25")
    cf.compact_var.set("1.05")
    cf.grid_enabled_var.set(True)
    cf.grid_spacing_var.set("1")
    path = tmp_path / "combined.tinproj.json"
    app.save_project_to(str(path))
    assert not app._dirty
    app.load_project_from(str(path))
    cf = app.calc_frame
    assert app.holes == [HOLE] and app.boundary_frame.holes == [HOLE]
    assert app.boundary_frame.breaklines == lines
    assert cf.loose_var.get() == "1.25" and cf.compact_var.get() == "1.05"
    assert cf.grid_enabled_var.get() and float(cf.grid_spacing_var.get()) == 1.0
    assert not app._dirty and not app.title().startswith("* ")
    cf._run_calculation()
    _wait(app, lambda: cf.result is not None)
    assert cf.result.total_cut == pytest.approx(EXACT_CUT - HOLE_CUT, abs=1e-6)
    assert cf.result.grid_check.total_cut == pytest.approx(cf.result.total_cut, abs=1e-6)
    # Every new persisted setting must engage the existing unsaved-changes guard.
    changes = {"trim_enabled_var": True, "max_edge_var": "50", "loose_var": "1.3",
               "compact_var": "1.1", "contours_enabled_var": False,
               "contour_interval_var": "0.2", "sections_enabled_var": True,
               "section_spacing_var": "5", "section_angle_var": "30",
               "layers_enabled_var": True, "layer_mode_var": "depth",
               "layer_values_var": "0.5", "uncertainty_enabled_var": True,
               "sigma_measure_var": "0.03", "recommend_var": "15"}
    for name, value in changes.items():
        app._mark_clean()
        getattr(cf, name).set(value)
        assert app._dirty, f"{name} failed to mark the project dirty"
        assert app.title().startswith("* "), name


def test_new_theme_preserves_save_notification_restore_and_step_navigation(main_window, tmp_path, monkeypatch):
    from gui.theme import COLORS

    app = main_window
    _load_gui(app)
    monkeypatch.setattr("tkinter.filedialog.asksaveasfilename",
                        lambda **kwargs: str(tmp_path / "themed.tinproj.json"))
    assert app._save_project()
    assert not app._dirty
    assert "工程已保存" in app.status_var.get()
    assert app.status_label.cget("bg") == COLORS["flash"]
    _wait(app, lambda: app._flash_after_id is None, timeout=5)
    assert app.status_label.cget("bg") == COLORS["status_bg"]
    app._goto_step(0)
    assert app.notebook.index("current") == 0
    assert app.progress_steps[0].cget("bg") == COLORS["accent_soft"]
    app._goto_step(2)
    assert app.notebook.index("current") == 2
    assert not app._dirty
