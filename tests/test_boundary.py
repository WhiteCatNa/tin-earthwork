from types import SimpleNamespace

import pytest

import gui.boundary_frame as boundary_module
from core.calculator import SurveyPoint
from core.geometry import segments_cross
from gui.boundary_frame import BoundaryFrame, scroll_steps


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


def test_scroll_steps_are_normalised_per_platform(monkeypatch):
    # Windows：matplotlib 给的 step 每格就是 1
    monkeypatch.setattr(boundary_module.sys, "platform", "win32")
    assert scroll_steps(SimpleNamespace(step=1.0, button="up")) == 1.0
    assert scroll_steps(SimpleNamespace(step=-10.0, button="down")) == -boundary_module.MAX_SCROLL_STEPS
    # Linux：step 为 0 时看 button
    assert scroll_steps(SimpleNamespace(step=0, button="down")) == -1.0
    # macOS：Tk 每格 delta 是 1，matplotlib 除以 120 后只剩 0.0083，要换算回来
    monkeypatch.setattr(boundary_module.sys, "platform", "darwin")
    assert scroll_steps(SimpleNamespace(step=1 / 120, button="up")) == pytest.approx(boundary_module.MAC_SCROLL_UNIT)
    assert scroll_steps(SimpleNamespace(step=-4 / 120, button="down")) == pytest.approx(-4 * boundary_module.MAC_SCROLL_UNIT)
    assert scroll_steps(SimpleNamespace(step=40 / 120, button="up")) == boundary_module.MAX_SCROLL_STEPS
