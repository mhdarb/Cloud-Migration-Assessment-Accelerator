from app.schemas.questions import InventorySummary
from app.services.question_planning import HeuristicQuestionPlanner, NoOpQuestionPlanner


def test_heuristic_question_planner_empty_when_no_signals():
    summary = InventorySummary(application_count=2, server_count=2, database_count=1)
    assert HeuristicQuestionPlanner().plan(summary) == []


def test_heuristic_question_planner_flags_unsupported_os():
    summary = InventorySummary(unsupported_os_servers=["mainframe-01", "aix-legacy-02"])
    questions = HeuristicQuestionPlanner().plan(summary)
    assert len(questions) == 1
    assert "mainframe-01" in questions[0].question
    assert "replatform" in questions[0].question.lower()


def test_heuristic_question_planner_flags_open_conflicts_and_nfr_gaps():
    summary = InventorySummary(open_conflict_count=2, nfr_gaps=["rto", "rpo"])
    questions = HeuristicQuestionPlanner().plan(summary)
    assert len(questions) == 2
    assert any("2 unresolved conflicting" in q.question for q in questions)
    assert any("rto" in q.question and "rpo" in q.question for q in questions)


def test_noop_question_planner_always_empty():
    summary = InventorySummary(unsupported_os_servers=["x"], open_conflict_count=5)
    assert NoOpQuestionPlanner().plan(summary) == []
