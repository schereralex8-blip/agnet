from datetime import date

import pytest

from homework_agent.tools.base import ToolError
from homework_agent.tools.tracker import (
    AddAssignmentTool,
    CompleteAssignmentTool,
    ListAssignmentsTool,
    Tracker,
    validate_due,
)


@pytest.fixture
def tracker(tmp_path):
    return Tracker(tmp_path / "assignments.json")


def test_add_and_list(tracker):
    AddAssignmentTool(tracker).run(title="Problem set 4", course="Calc II", due="2026-09-18")
    listing = ListAssignmentsTool(tracker).run()
    assert "Problem set 4" in listing
    assert "Calc II" in listing


def test_list_is_soonest_first(tracker):
    add = AddAssignmentTool(tracker)
    add.run(title="Later", due="2026-12-01")
    add.run(title="Sooner", due="2026-09-20")
    add.run(title="Undated")
    lines = ListAssignmentsTool(tracker).run().splitlines()
    assert [line.split("] ")[1].split()[0] for line in lines] == ["Sooner", "Later", "Undated"]


def test_overdue_and_today_are_called_out(tracker):
    today = date(2026, 9, 14)
    tracker.add("Late lab", None, "2026-09-10", None)
    tracker.add("Quiz", None, today.isoformat(), None)
    listing = tracker.listing(today=today)
    assert "4d OVERDUE" in listing
    assert "TODAY" in listing


def test_completing_hides_it_from_the_default_list(tracker):
    AddAssignmentTool(tracker).run(title="Essay")
    item_id = tracker.load()[0].id
    CompleteAssignmentTool(tracker).run(assignment_id=item_id)
    assert ListAssignmentsTool(tracker).run() == "nothing outstanding"
    assert "Essay" in ListAssignmentsTool(tracker).run(include_done=True)


def test_unknown_id_tells_the_model_what_to_do(tracker):
    with pytest.raises(ToolError, match="list_assignments"):
        CompleteAssignmentTool(tracker).run(assignment_id="zzzzzz")


def test_bad_due_date_is_rejected():
    with pytest.raises(ToolError, match="YYYY-MM-DD"):
        validate_due("next friday")
    assert validate_due("  2026-09-18 ") == "2026-09-18"
    assert validate_due("") is None


def test_state_survives_a_reload(tracker, tmp_path):
    AddAssignmentTool(tracker).run(title="Reading", notes="chapters 4-5")
    reopened = Tracker(tmp_path / "assignments.json")
    assert reopened.load()[0].notes == "chapters 4-5"


def test_corrupt_file_is_reported_not_crashed(tmp_path):
    path = tmp_path / "assignments.json"
    path.write_text("{ not json")
    with pytest.raises(ToolError, match="unreadable"):
        Tracker(path).load()


def test_empty_list_message(tracker):
    assert tracker.listing() == "nothing outstanding"
