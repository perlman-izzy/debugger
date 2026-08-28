import unittest

from epistemic import (
    Attempt,
    AuditDecision,
    DurableState,
    Evidence,
    EvidenceStatus,
    Hypothesis,
    RetryLevel,
)


class EpistemicRetryTests(unittest.TestCase):
    def setUp(self):
        self.state = DurableState("fix auth bug", "cp-0")
        self.state.add_fact(Evidence("e1", EvidenceStatus.VERIFIED_FACT, "test_auth fails with 401"))
        self.state.add_hypothesis(
            Hypothesis("h1", "parser normalization causes failure", "normalization differs", "inspect parser I/O")
        )

    def attempt(self, n, decision=AuditDecision.REJECT, independent=True, **kwargs):
        return Attempt(
            f"a{n}", "sg-auth", "h1", f"run-{n}", decision, independent,
            evidence_ids=(f"rej-{n}",), **kwargs
        )

    def test_first_rejection_allows_implementation_retry(self):
        self.assertEqual(self.state.record_attempt(self.attempt(1)), RetryLevel.IMPLEMENTATION)

    def test_second_independent_rejection_forces_epistemic_reset(self):
        self.state.record_attempt(self.attempt(1))
        self.assertEqual(self.state.record_attempt(self.attempt(2)), RetryLevel.EPISTEMIC_RESET)
        self.assertEqual(self.state.hypotheses["h1"].status, EvidenceStatus.FALSIFIED_HYPOTHESIS)

    def test_executor_self_rejection_does_not_count_toward_reset_threshold(self):
        self.state.record_attempt(self.attempt(1, independent=False))
        self.state.record_attempt(self.attempt(2, independent=True))
        self.assertEqual(self.state.record_attempt(self.attempt(3, independent=False)), RetryLevel.IMPLEMENTATION)

    def test_non_independent_accept_cannot_advance_authority(self):
        with self.assertRaises(ValueError):
            self.state.record_attempt(self.attempt(1, decision=AuditDecision.ACCEPT, independent=False))

    def test_independent_accept_completes(self):
        self.assertEqual(
            self.state.record_attempt(self.attempt(1, decision=AuditDecision.ACCEPT, independent=True)),
            RetryLevel.COMPLETE,
        )

    def test_inconclusive_requires_evidence_not_blind_retry(self):
        self.assertEqual(
            self.state.record_attempt(self.attempt(1, decision=AuditDecision.INCONCLUSIVE)),
            RetryLevel.ACQUIRE_EVIDENCE,
        )

    def test_transient_failure_stays_level_1(self):
        self.assertEqual(
            self.state.record_attempt(self.attempt(1, transient_failure=True)),
            RetryLevel.TRANSIENT,
        )

    def test_contaminated_trajectory_forces_level_4(self):
        self.assertEqual(
            self.state.record_attempt(self.attempt(1, trajectory_contaminated=True)),
            RetryLevel.TRAJECTORY_RESET,
        )

    def test_reset_packet_contains_verified_facts_and_falsified_hypotheses_only(self):
        self.state.record_attempt(self.attempt(1))
        self.state.record_attempt(self.attempt(2))
        packet = self.state.reset_packet("sg-auth")
        self.assertEqual(packet["last_accepted_checkpoint"], "cp-0")
        self.assertEqual(packet["required_competing_hypotheses"], 2)
        self.assertTrue(packet["require_discriminating_test_before_edit"])
        self.assertEqual(packet["verified_facts"][0]["claim"], "test_auth fails with 401")
        self.assertEqual(packet["falsified_hypotheses"][0]["hypothesis_id"], "h1")
        self.assertNotIn("reasoning_summary", packet)


class ResetPacketAdversarialTests(unittest.TestCase):
    """Adversarial tests for reset packet invariants."""

    def setUp(self):
        self.state = DurableState("objective", "cp-1")
        self.state.add_fact(Evidence("e1", EvidenceStatus.VERIFIED_FACT, "fact one"))
        self.state.add_fact(Evidence("e2", EvidenceStatus.VERIFIED_FACT, "fact two"))
        self.state.add_hypothesis(
            Hypothesis("h1", "hypothesis one", "obs 1", "test 1")
        )
        self.state.add_hypothesis(
            Hypothesis("h2", "hypothesis two", "obs 2", "test 2")
        )
        # Falsify h1 via two independent rejections
        self.state.record_attempt(Attempt("a1", "sg-1", "h1", "run-1", AuditDecision.REJECT, True, evidence_ids=("rej-1",)))
        self.state.record_attempt(Attempt("a2", "sg-1", "h1", "run-2", AuditDecision.REJECT, True, evidence_ids=("rej-2",)))
        # h2 remains HYPOTHESIS (only one rejection)
        self.state.record_attempt(Attempt("a3", "sg-1", "h2", "run-3", AuditDecision.REJECT, True, evidence_ids=("rej-3",)))

    def test_reset_packet_excludes_non_falsified_hypotheses(self):
        packet = self.state.reset_packet("sg-1")
        falsified_ids = {h["hypothesis_id"] for h in packet["falsified_hypotheses"]}
        self.assertIn("h1", falsified_ids)
        self.assertNotIn("h2", falsified_ids, "non-falsified hypothesis must not appear in reset packet")

    def test_reset_packet_includes_all_verified_facts(self):
        packet = self.state.reset_packet("sg-1")
        fact_claims = {f["claim"] for f in packet["verified_facts"]}
        self.assertEqual(fact_claims, {"fact one", "fact two"})

    def test_reset_packet_excludes_attempts_and_evidence_narrative(self):
        packet = self.state.reset_packet("sg-1")
        # Only allowed top-level keys
        allowed_keys = {
            "objective",
            "subgoal_id",
            "last_accepted_checkpoint",
            "verified_facts",
            "falsified_hypotheses",
            "required_competing_hypotheses",
            "require_discriminating_test_before_edit",
        }
        self.assertEqual(set(packet.keys()), allowed_keys)
        # No attempt IDs, no evidence IDs beyond falsifying_evidence refs
        for h in packet["falsified_hypotheses"]:
            self.assertIn("falsifying_evidence", h)
            self.assertIsInstance(h["falsifying_evidence"], tuple)
        for f in packet["verified_facts"]:
            self.assertIn("evidence_id", f)
            self.assertIn("claim", f)
            self.assertEqual(set(f.keys()), {"evidence_id", "claim"})

    def test_reset_packet_empty_falsified_hypotheses_when_none(self):
        fresh = DurableState("obj", "cp-0")
        fresh.add_fact(Evidence("e1", EvidenceStatus.VERIFIED_FACT, "fact"))
        fresh.add_hypothesis(Hypothesis("h1", "claim", "obs", "test"))
        packet = fresh.reset_packet("sg-1")
        self.assertEqual(packet["falsified_hypotheses"], [])
        self.assertEqual(len(packet["verified_facts"]), 1)

    def test_reset_packet_empty_verified_facts_when_none(self):
        fresh = DurableState("obj", "cp-0")
        fresh.add_hypothesis(Hypothesis("h1", "claim", "obs", "test"))
        fresh.record_attempt(Attempt("a1", "sg-1", "h1", "run-1", AuditDecision.REJECT, True, evidence_ids=("rej-1",)))
        fresh.record_attempt(Attempt("a2", "sg-1", "h1", "run-2", AuditDecision.REJECT, True, evidence_ids=("rej-2",)))
        packet = fresh.reset_packet("sg-1")
        self.assertEqual(packet["verified_facts"], [])
        self.assertEqual(len(packet["falsified_hypotheses"]), 1)


if __name__ == "__main__":
    unittest.main()