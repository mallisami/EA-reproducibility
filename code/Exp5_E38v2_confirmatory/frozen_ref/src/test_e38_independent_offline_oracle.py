"""Only offline design tests. No Kubernetes or real observations are produced."""
import unittest
from dataclasses import replace
from e38_independent_offline_oracle import (
    State, truth_from_state, hybrid, asymmetric_goals, symmetric_goals,
    enumerate_exact_minimum, certificate_rejects_all, validate_trial_accounting,
    DECISION_UID, REPLACEMENT_UID)

class ProtocolTruthTests(unittest.TestCase):
    def setUp(self): self.initial=State()
    def test_valid_stable_and_irrelevant(self):
        self.assertEqual(truth_from_state(State()),'EXECUTE')
        self.assertEqual(truth_from_state(replace(self.initial,b=False)),'EXECUTE')
    def test_evidence_invalidation_but_not_single_literal(self):
        self.assertEqual(truth_from_state(replace(self.initial,a=False)),'EXECUTE')
        self.assertEqual(truth_from_state(replace(self.initial,a=False,c=False,e=False)),'ABORT_REPLAN')
    def test_target_and_scope(self):
        self.assertEqual(truth_from_state(replace(self.initial,uid=REPLACEMENT_UID)),'ABORT_REPLAN')
        self.assertEqual(truth_from_state(replace(self.initial,scope=False)),'ABORT_REPLAN')
    def test_hybrid_cached_invalid_evidence_requires_fresh_cut(self):
        stale=replace(self.initial,a=False,c=False,e=False)
        self.assertEqual(truth_from_state(hybrid(self.initial,stale,())),'EXECUTE')
        self.assertEqual(truth_from_state(hybrid(self.initial,stale,('a','c','e'))),'ABORT_REPLAN')
    def test_asymmetric_optimum_is_five(self):
        goals=asymmetric_goals()
        self.assertTrue(all(not g.valid for g in goals))
        exact=enumerate_exact_minimum(self.initial,goals)
        self.assertEqual(exact,(('a','c','e','uid','scope'),))
        self.assertTrue(certificate_rejects_all(self.initial,goals,exact[0]))
    def test_symmetric_optimum_is_eight(self):
        goals=symmetric_goals(); self.assertEqual(len(goals),10)
        exact=enumerate_exact_minimum(self.initial,goals)
        self.assertEqual(exact,(('a','b','c','d','e','f','uid','scope'),))
    def test_empty_invalid_set_has_empty_certificate(self):
        self.assertEqual(enumerate_exact_minimum(self.initial,[]),((),))
    def test_unknown_query_fails(self):
        with self.assertRaises(ValueError):hybrid(self.initial,self.initial,('phantom',))

class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.ok=dict(trial_id='offline-fixture',scenario='stable',method='DCJI',cell='idle-serial',
                     block_id=1,seed=1,guard_order='DCJI-first',namespace='test-disposable',
                     decision_uid=DECISION_UID,observed_uid=DECISION_UID,
                     current_justification=True,current_scope_safe=True,
                     oracle_decision='EXECUTE',guard_decision='EXECUTE',coherent=True,get_count=5,
                     response_body_bytes=350,refresh_latency_ms=11.,gate_latency_ms=13.,
                     delete_sent=True,uid_precondition_sent=True,server_http_status=200,
                     post_effect_uid=None,pod_absent=True,first_effect_elapsed_ms=20.,
                     gate_deadline_missed=False,effect_deadline_missed=False,
                     failed=False,aborted=False,timeout=False,log_reference='OFFLINE_FIXTURE_NOT_AN_EXPERIMENT')
    def test_accepted_fixture(self):self.assertTrue(validate_trial_accounting(self.ok)[0])
    def test_missing_data_not_accepted(self):
        bad=dict(self.ok);bad.pop('get_count');self.assertFalse(validate_trial_accounting(bad)[0])
    def test_unconditioned_delete_not_accepted(self):
        self.assertFalse(validate_trial_accounting(dict(self.ok,uid_precondition_sent=False))[0])
    def test_conflict_is_not_effect(self):
        self.assertFalse(validate_trial_accounting(dict(self.ok,server_http_status=409))[0])
    def test_no_action_after_abort(self):
        self.assertFalse(validate_trial_accounting(dict(self.ok,guard_decision='ABORT_REPLAN'))[0])
    def test_incoherent_cannot_execute(self):
        self.assertFalse(validate_trial_accounting(dict(self.ok,coherent=False))[0])
    def test_wrong_target_dispatch_flags_for_review(self):
        self.assertFalse(validate_trial_accounting(dict(self.ok,observed_uid=REPLACEMENT_UID))[0])
    def test_oracle_disagreement_must_be_kept(self):
        self.assertFalse(validate_trial_accounting(dict(self.ok,oracle_decision='ABORT_REPLAN'))[0])
    def test_no_pod_effect_without_delete(self):
        self.assertFalse(validate_trial_accounting(dict(self.ok,delete_sent=False))[0])

if __name__=='__main__':unittest.main(verbosity=2)
