from app.models.entities import WorkflowStage
from app.services.workflow import append_follow_up


def test_follow_up_log_appended(assessment):
    log = append_follow_up(
        assessment, event="manual_note", detail={"note": "tighten OS conflict rules"}
    )
    assert log[-1]["event"] == "manual_note"
    assert "tighten" in log[-1]["detail"]["note"]
    assert assessment.workflow_stage in {
        WorkflowStage.research,
        WorkflowStage.implement,
        WorkflowStage.review,
        WorkflowStage.follow_up,
        WorkflowStage.plan,
    }
