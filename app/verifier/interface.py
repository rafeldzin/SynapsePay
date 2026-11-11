from typing import Optional
from app.models.schema import Milestone, Deliverable, Verdict, VerdictEnum, CriterionVerdict


def apply_coverage_rule(verdict: Verdict) -> Verdict:
    """
    Deterministic coverage rule:
    - Any criterion with PASS but empty/None evidence is downgraded to NEEDS_REVIEW.
    - Overall verdict is PASS only if ALL criteria are PASS after downgrade and injection_flag is False.
    - If injection_flag is True, overall verdict is FAIL.
    """
    updated_criteria = []
    has_downgrade = False

    for c in verdict.criteria:
        crit = c.model_copy()
        if crit.verdict == VerdictEnum.PASS and (crit.evidence is None or not crit.evidence.strip()):
            crit.verdict = VerdictEnum.NEEDS_REVIEW
            has_downgrade = True
        updated_criteria.append(crit)

    reasons = list(verdict.reasons)

    if verdict.injection_flag:
        overall_verdict = VerdictEnum.FAIL
        if "Prompt injection or override attempt detected in deliverable." not in reasons:
            reasons.append("Prompt injection or override attempt detected in deliverable.")
    else:
        all_pass = (
            len(updated_criteria) > 0
            and all(crit.verdict == VerdictEnum.PASS for crit in updated_criteria)
        )
        has_fail = any(crit.verdict == VerdictEnum.FAIL for crit in updated_criteria)

        if all_pass:
            overall_verdict = VerdictEnum.PASS
        elif has_fail:
            overall_verdict = VerdictEnum.FAIL
        else:
            overall_verdict = VerdictEnum.NEEDS_REVIEW
            if has_downgrade and "Criteria downgraded to NEEDS_REVIEW due to missing evidence." not in reasons:
                reasons.append("Criteria downgraded to NEEDS_REVIEW due to missing evidence.")

    return Verdict(
        milestone_id=verdict.milestone_id,
        verdict=overall_verdict,
        reasons=reasons,
        injection_flag=verdict.injection_flag,
        criteria=updated_criteria,
    )


def verify(milestone: Milestone, deliverable: Deliverable) -> Verdict:
    """
    Stub verifier:
    - INJECTION / OVERRIDE => FAIL + injection_flag True
    - Otherwise => PASS with dummy evidence string
    - Applies coverage rule
    """
    target_id = deliverable.milestone_id if deliverable.milestone_id else milestone.id
    content_upper = (deliverable.content or "").upper()

    if "INJECTION" in content_upper or "OVERRIDE" in content_upper:
        raw_verdict = Verdict(
            milestone_id=target_id,
            verdict=VerdictEnum.FAIL,
            reasons=["Prompt injection or override attempt detected in deliverable."],
            injection_flag=True,
            criteria=[
                CriterionVerdict(
                    criterion="Instruction safety & prompt integrity",
                    verdict=VerdictEnum.FAIL,
                    evidence="Adversarial keyword detected in deliverable content.",
                )
            ],
        )
    else:
        raw_verdict = Verdict(
            milestone_id=target_id,
            verdict=VerdictEnum.PASS,
            reasons=["Deliverable meets acceptance criteria."],
            injection_flag=False,
            criteria=[
                CriterionVerdict(
                    criterion=milestone.title or "Milestone acceptance criteria",
                    verdict=VerdictEnum.PASS,
                    evidence="Deliverable content verified against acceptance specifications.",
                )
            ],
        )

    return apply_coverage_rule(raw_verdict)
