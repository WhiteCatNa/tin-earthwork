from core.calculator import SurveyPoint
from utils.recent_projects import forget_recent, load_recent, remember_recent
from utils.survey_edit import (
    add_survey_point,
    delete_survey_point_at,
    offset_survey_data,
    update_survey_point,
)


def test_edit_add_delete_survey_points():
    points = [
        SurveyPoint("A", 0, 0, 12),
        SurveyPoint("B", 1, 0, 8),
        SurveyPoint("C", 0, 1, 10),
    ]
    add_survey_point(points, SurveyPoint("D", 1, 1, 9))
    assert [point.id for point in points] == ["A", "B", "C", "D"]
    update_survey_point(points, 1, z=8.5)
    assert points[1].z == 8.5
    removed = delete_survey_point_at(points, 0)
    assert removed.id == "A"
    assert [point.id for point in points] == ["B", "C", "D"]


def test_offset_survey_points_and_boundary():
    points = [
        SurveyPoint("A", 0, 0, 12, design_z=11, has_design_z=True, delta_z=1),
        SurveyPoint("B", 1, 0, 8, design_z=9, has_design_z=True, delta_z=-1),
    ]
    boundary = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
    moved = offset_survey_data(points, 100, 200, 0.5, boundary)
    assert points[0].x == 100
    assert points[0].y == 200
    assert points[0].z == 12.5
    assert points[0].design_z == 11.5
    assert points[0].delta_z == 1.0
    assert moved == [(100.0, 200.0), (101.0, 200.0), (101.0, 201.0)]


def test_remember_and_reopen_recent_projects(tmp_path, monkeypatch):
    recent = tmp_path / "recent.json"
    monkeypatch.setenv("TIN_EARTHWORK_RECENT", str(recent))
    first = tmp_path / "a.tinproj.json"
    second = tmp_path / "b.tinproj.json"
    first.write_text("{}", encoding="utf-8")
    second.write_text("{}", encoding="utf-8")

    remember_recent(first)
    remembered = remember_recent(second)
    assert remembered[0] == str(second.resolve())
    assert remembered[1] == str(first.resolve())

    missing = tmp_path / "gone.tinproj.json"
    remember_recent(missing)
    forget_recent(missing)
    assert str(missing.resolve()) not in load_recent()


def test_import_frame_edit_offset_and_recent_project(main_window, tmp_path):
    app = main_window
    app.import_frame.points = [
        SurveyPoint("A", 0, 0, 12),
        SurveyPoint("B", 1, 0, 8),
    ]
    app.import_frame._update_preview()
    app.import_frame.update_survey_point_at(0, z=12.2)
    app.import_frame.add_survey_point(SurveyPoint("C", 0, 1, 10))
    app.import_frame.delete_survey_point_at(1)
    assert [point.id for point in app.import_frame.points] == ["A", "C"]
    assert app.import_frame.points[0].z == 12.2

    app.import_frame._confirm_points()
    app.boundary_frame.set_boundary([(0, 0), (1, 0), (0, 1)])
    app._on_boundary_set(app.boundary_frame.boundary)
    app.import_frame.apply_coordinate_offset(10, 20, 1)
    assert app.import_frame.points[0].x == 10
    assert app.boundary == [(10, 20), (11, 20), (10, 21)]

    project = tmp_path / "job.tinproj.json"
    app.save_project_to(project)
    assert str(project.resolve()) in load_recent()

    app.import_frame.points = [SurveyPoint("Z", 9, 9, 9)]
    app.points = app.import_frame.points
    app.open_recent_project(project)
    assert [point.id for point in app.points] == ["A", "C"]
    assert app.points[0].x == 10
