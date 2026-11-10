from app.models.schema import Milestone, Deliverable, Verdict, VerdictEnum


def verify(milestone: Milestone, deliverable: Deliverable) -> Verdict:
    """
    Mock stub verifier interface.
    
    Checks the deliverable content against milestone acceptance criteria.
    Temporarily flags content containing 'INJECTION' or 'OVERRIDE' as FAIL.
    """
    target_id = deliverable.milestone_id if deliverable.milestone_id else milestone.id
    content_upper = (deliverable.content or "").upper()

    if "INJECTION" in content_upper or "OVERRIDE" in content_upper:
        return Verdict(
            milestone_id=target_id,
            verdict=VerdictEnum.FAIL,
            score=0.0,
            reasons=["Prompt injection or override attempt detected in deliverable."],
            injection_flag=True,
        )

    return Verdict(
        milestone_id=target_id,
        verdict=VerdictEnum.PASS,
        score=1.0,
        reasons=["Deliverable meets acceptance criteria."],
        injection_flag=False,
    )
