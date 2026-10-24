from core.calculator import SurveyPoint
from utils.data_handler import save_project


def test_apply_project_restores_points_boundary_and_design_settings(main_window, tmp_path):
    app = main_window
    points = [
        SurveyPoint("A", 0, 0, 12, design_z=11, delta_z=1, has_design_z=True),
        SurveyPoint("B", 1, 0, 8, design_z=9, delta_z=-1, has_design_z=True),
        SurveyPoint("C", 0, 1, 10, design_z=10, delta_z=0, has_design_z=True),
    ]
    boundary = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    path = tmp_path / "site.tinproj.json"
    save_project(
        path,
        points,
        boundary,
        design_elevation=10,
        use_partition=True,
        partition={"A": 11, "B": 9, "C": 10},
        project_name="试验段A",
    )

    app.load_project_from(path)
    assert [point.id for point in app.points] == ["A", "B", "C"]
    assert app.boundary == boundary
    assert app.notebook.tab(1, "state") == "normal"
    assert app.calc_frame is not None
    assert app.calc_frame.use_partition_var.get()
    assert app.calc_frame.partition_data["B"] == 9
    assert app.calc_frame.calculator.points[0].design_z == 11
    assert "试验段A" in app.project_label_var.get()
