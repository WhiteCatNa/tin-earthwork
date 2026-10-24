"""Merged engineering settings must survive project saves without changing old callers."""
import json

from core.breaklines import Breakline
from core.calculator import SurveyPoint
from utils.data_handler import load_project, save_project


def test_legacy_positional_arguments_keep_grid_and_breakline_settings(tmp_path):
    path = tmp_path / "legacy-call.tinproj.json"
    points = [SurveyPoint("A", 0, 0, 10)]
    lines = [Breakline([(0, 0, 10), (10, 0, 11)], name="ridge")]
    save_project(path, points, [], 8.0, False, None, "site", "flat", None,
                 None, "", True, 5.0, lines, [])
    loaded = load_project(path)
    assert loaded["grid_enabled"] is True
    assert loaded["grid_spacing"] == 5.0
    assert loaded["breaklines"] == lines
    assert loaded["design_points"] == []


def test_breaklines_and_holes_survive_same_project(tmp_path):
    path = tmp_path / "combined.tinproj.json"
    points = [SurveyPoint("A", 0, 0, 10)]
    boundary = [(0, 0), (10, 0), (10, 10), (0, 10)]
    holes = [[(2, 2), (3, 2), (3, 3), (2, 3)]]
    lines = [Breakline([(0, 5, 10), (10, 5, 11)], name="ridge")]
    save_project(path, points, boundary, breaklines=lines, holes=holes, max_edge_length=20)
    loaded = load_project(path)
    assert loaded["boundary"] == boundary
    assert loaded["holes"] == holes
    assert loaded["breaklines"] == lines
    assert loaded["max_edge_length"] == 20
    assert json.loads(path.read_text())["format_version"] == 3


def test_new_topology_prevents_legacy_reader_silently_ignoring_it(tmp_path):
    path = tmp_path / "topology.tinproj.json"
    points = [SurveyPoint("A", 0, 0, 10)]
    save_project(path, points, [], holes=[[(1, 1), (2, 1), (1, 2)]])
    assert json.loads(path.read_text())["format_version"] == 3
    design = [SurveyPoint("D", 0, 0, 8)]
    save_project(path, points, [], design_mode="design_tin", design_points=design)
    assert json.loads(path.read_text())["format_version"] == 3
    loaded = load_project(path)
    assert loaded["design_mode"] == "design_tin"
    assert [(p.id, p.z) for p in loaded["design_points"]] == [("D", 8)]
    save_project(path, points, [])
    assert json.loads(path.read_text())["format_version"] == 2
