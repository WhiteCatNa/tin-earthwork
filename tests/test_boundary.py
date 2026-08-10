from core.calculator import SurveyPoint
from gui.boundary_frame import BoundaryFrame


def test_boundary_segment_intersection():
    assert BoundaryFrame._segments_intersect((0, 0), (1, 1), (0, 1), (1, 0))
    assert not BoundaryFrame._segments_intersect((0, 0), (1, 0), (0, 1), (1, 1))


def test_snap_to_point_uses_nearest_coordinate():
    frame = object.__new__(BoundaryFrame)
    frame.points = [SurveyPoint("A", 0, 0, 1), SurveyPoint("B", 10, 10, 2)]
    frame._point_xy = None
    assert frame._snap_to_point(0.5, 0.5, threshold=2) == (0.0, 0.0)
    assert frame._snap_to_point(5, 5, threshold=1) == (5, 5)
