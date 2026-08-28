from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


class EvidenceStatus(str, Enum):
    VERIFIED_FACT = "VERIFIED_FACT"
    HYPOTHESIS = "HYPOTHESIS"
    FALSIFIED_HYPOTHESIS = "FALSIFIED_HYPOTHESIS"
    UNKNOWN = "UNKNOWN"


class AuditDecision(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    INCONCLUSIVE = "INCONCLUSIVE"


class RetryLevel(str, Enum):
    TRANSIENT = "LEVEL_1_TRANSIENT"
    IMPLEMENTATION = "LEVEL_2_IMPLEMENTATION"
    EPISTEMIC_RESET = "LEVEL_3_EPISTEMIC_RESET"
    TRAJECTORY_RESET = "LEVEL_4_TRAJECTORY_RESET"
    ACQUIRE_EVIDENCE = "ACQUIRE_EVIDENCE"
    COMPLETE = "COMPLETE"


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    status: EvidenceStatus
    claim: str
    execution_id: str | None = None
    supports: tuple[str, ...] = ()
    contradicts: tuple[str, ...] = ()


@dataclass
class Hypothesis:
    hypothesis_id: str
    claim: str
    predicted_observation: str
    discriminating_test: str
    status: EvidenceStatus = EvidenceStatus.HYPOTHESIS
    rejection_count: int = 0
    falsifying_evidence: list[str] = field(default_factory=list)

    def reject(self, evidence_ids: Iterable[str], *, decisive: bool) -> None:
        self.rejection_count += 1
        for evidence_id in evidence_ids:
            if evidence_id not in self.falsifying_evidence:
                self.falsifying_evidence.append(evidence_id)
        if decisive:
            self.status = EvidenceStatus.FALSIFIED_HYPOTHESIS


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    subgoal_id: str
    hypothesis_id: str
    execution_id: str
    audit_decision: AuditDecision
    independent_auditor: bool
    evidence_ids: tuple[str, ...] = ()
    transient_failure: bool = False
    trajectory_contaminated: bool = False


@dataclass
class DurableState:
    objective: str
    last_accepted_checkpoint: str
    facts: dict[str, Evidence] = field(default_factory=dict)
    hypotheses: dict[str, Hypothesis] = field(default_factory=dict)
    attempts: list[Attempt] = field(default_factory=list)

    def add_fact(self, evidence: Evidence) -> None:
        if evidence.status != EvidenceStatus.VERIFIED_FACT:
            raise ValueError("only VERIFIED_FACT may enter facts")
        self.facts[evidence.evidence_id] = evidence

    def add_hypothesis(self, hypothesis: Hypothesis) -> None:
        self.hypotheses[hypothesis.hypothesis_id] = hypothesis

    def record_attempt(self, attempt: Attempt) -> RetryLevel:
        self.attempts.append(attempt)

        if attempt.audit_decision == AuditDecision.ACCEPT:
            if not attempt.independent_auditor:
                raise ValueError("executor/non-independent acceptance cannot advance authority")
            return RetryLevel.COMPLETE

        if attempt.trajectory_contaminated:
            return RetryLevel.TRAJECTORY_RESET

        if attempt.transient_failure:
            return RetryLevel.TRANSIENT

        if attempt.audit_decision == AuditDecision.INCONCLUSIVE:
            return RetryLevel.ACQUIRE_EVIDENCE

        if attempt.audit_decision != AuditDecision.REJECT:
            raise ValueError(f"unsupported audit decision: {attempt.audit_decision}")

        hypothesis = self.hypotheses[attempt.hypothesis_id]
        independent_rejections = sum(
            1
            for a in self.attempts
            if a.subgoal_id == attempt.subgoal_id
            and a.hypothesis_id == attempt.hypothesis_id
            and a.audit_decision == AuditDecision.REJECT
            and a.independent_auditor
        )

        if attempt.independent_auditor:
            hypothesis.reject(attempt.evidence_ids, decisive=independent_rejections >= 2)

        if independent_rejections >= 2:
            hypothesis.status = EvidenceStatus.FALSIFIED_HYPOTHESIS
            return RetryLevel.EPISTEMIC_RESET

        return RetryLevel.IMPLEMENTATION

    def reset_packet(self, subgoal_id: str) -> dict:
        falsified = [
            {
                "hypothesis_id": h.hypothesis_id,
                "claim": h.claim,
                "falsifying_evidence": tuple(h.falsifying_evidence),
            }
            for h in self.hypotheses.values()
            if h.status == EvidenceStatus.FALSIFIED_HYPOTHESIS
        ]
        verified_facts = [
            {"evidence_id": e.evidence_id, "claim": e.claim}
            for e in self.facts.values()
        ]
        return {
            "objective": self.objective,
            "subgoal_id": subgoal_id,
            "last_accepted_checkpoint": self.last_accepted_checkpoint,
            "verified_facts": verified_facts,
            "falsified_hypotheses": falsified,
            "required_competing_hypotheses": 2,
            "require_discriminating_test_before_edit": True,
        }
