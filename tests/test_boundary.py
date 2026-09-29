from core.calculator import SurveyPoint
from core.geometry import segments_cross
from gui.boundary_frame import BoundaryFrame


def test_boundary_segment_intersection():
    assert segments_cross((0, 0), (1, 1), (0, 1), (1, 0))
    assert not segments_cross((0, 0), (1, 0), (0, 1), (1, 1))


def test_snap_to_point_uses_nearest_coordinate():
    frame = object.__new__(BoundaryFrame)
    frame.points = [SurveyPoint("A", 0, 0, 1), SurveyPoint("B", 10, 10, 2)]
    frame._point_xy = None
    assert frame._snap_to_point(0.5, 0.5, threshold=2) == (0.0, 0.0)
    assert frame._snap_to_point(5, 5, threshold=1) == (5, 5)


def test_boundary_validity_and_overlap_checks():
    frame = object.__new__(BoundaryFrame)
    frame.points = [SurveyPoint("A", 0, 0, 1), SurveyPoint("B", 10, 10, 2)]
    frame.boundary = [(0, 0), (10, 10), (10, 0), (0, 10)]
    assert not frame._boundary_is_valid()
    frame.boundary = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert frame._boundary_is_valid()
    assert frame._overlaps_points()
    frame.boundary = [(100, 100), (110, 100), (110, 110)]
    assert not frame._overlaps_points()
