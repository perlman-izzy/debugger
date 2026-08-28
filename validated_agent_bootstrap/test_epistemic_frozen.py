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


class FrozenEpistemicRetryTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
