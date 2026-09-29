import queue
from unittest.mock import Mock

from gui.calculation_frame import CalculationFrame


def test_worker_events_are_dispatched_on_poll():
    frame = object.__new__(CalculationFrame)
    result = object()
    frame._closing = False
    frame._worker_poll_after_id = "poll-id"
    frame._worker_events = queue.Queue()
    frame._worker_events.put(("done", result))
    frame._worker_thread = Mock()
    frame._on_calculation_done = Mock()
    frame._on_calculation_error = Mock()
    frame._update_calculation_progress = Mock()
    frame._schedule_worker_poll = Mock()

    frame._poll_worker_events()

    assert frame.result is result
    assert frame._worker_thread is None
    frame._on_calculation_done.assert_called_once_with()
    frame._schedule_worker_poll.assert_not_called()


def test_worker_events_are_ignored_while_closing():
    frame = object.__new__(CalculationFrame)
    frame._closing = True
    frame._worker_poll_after_id = "poll-id"
    frame._worker_events = queue.Queue()
    frame._worker_events.put(("done", object()))
    frame._on_calculation_done = Mock()

    frame._poll_worker_events()

    assert frame._worker_poll_after_id is None
    assert frame._worker_events.qsize() == 1
    frame._on_calculation_done.assert_not_called()


def test_parse_partition_text_reports_bad_lines():
    from gui.calculation_frame import parse_partition_text

    data, bad = parse_partition_text("A,15.3\nB，14.8\nC\t15\n\nD,abc\nE,1,2\n")
    assert data == {"A": 15.3, "B": 14.8, "C": 15.0}
    assert bad == ["D,abc", "E,1,2"]
