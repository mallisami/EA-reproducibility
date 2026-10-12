"""Oracle tests: every E39 action/arm path and the pre-registered edge cases."""
import copy
import random

import pytest

import e39_testkit as kit
import synth_e39 as syn

SLOW = {"d_commit": 5.0}          # DELETE/CREATE commits 5 ms after receipt -> a delta-0 write lands first


# ----------------------------------------------------------------------------- E38v2 semantics carried over
def test_a1_write_before_delete_commit_is_invalid_executed():
    row, tr, *_ = kit.one("A", "A1", "async_scope", delta=0.0, P=SLOW)
    assert row["primary_class"] == "INVALID_EXECUTED" == tr["truth_primary"]
    assert row["secondary_class"] == "INVALID_EXECUTED" and row["rules_agree"] == 1
    assert row["action_rv"] > row["first_write_rv"]


def test_a1_write_after_delete_commit_is_valid_executed():
    row, tr, *_ = kit.one("A", "A1", "async_scope", delta=50.0)
    assert row["primary_class"] == "VALID_EXECUTED" == tr["truth_primary"]
    assert row["primary_reason"] == "write_committed_after_action(rv)"


def test_a3_webhook_deny_is_correct_abort():
    row, tr, *_ = kit.one("A", "A3", "async_scope", delta=0.0)
    assert row["decider"] == "webhook" and row["primary_class"] == "CORRECT_ABORT" == tr["truth_primary"]
    assert "webhook_joined_by_pod_uid" in row["flags"]


def test_gate_unknown_coherence_is_fail_closed_unknown():
    row, tr, raw, audit, wh = kit.one("A", "A1", "async_scope", delta=50.0)
    raw["gate"]["decision"] = "UNKNOWN_COHERENCE"
    raw["delete"].update(dispatched=False, http_status=None)
    r = kit.classify(raw, audit, wh)
    assert r["primary_class"] == "UNKNOWN" and r["primary_reason"] == "gate_unknown_coherence"


@pytest.mark.parametrize("arm", ["A1", "A4d"])
def test_uid_rebind_control_is_uid_blocked(arm):
    row, tr, *_ = kit.one("A", arm, "uid_rebind_control")
    assert row["primary_class"] == "UID_BLOCKED" and row["conflict_kind"] == "uid"


def test_evidence_last_literal_fixture_mismatch_is_unknown():
    row, tr, raw, audit, wh = kit.one("A", "A1", "async_evidence_last_literal", delta=50.0)
    raw["fixture"]["initial_values"]["evidence-c"] = "true"
    assert kit.classify(raw, audit, wh)["primary_reason"] == "fixture_condition_mismatch(c_or_e_not_false)"


# ----------------------------------------------------------------------------- A4d: resourceVersion precondition
def test_a4d_409_precondition_conflict_from_writer_is_correct_abort():
    row, tr, *_ = kit.one("A", "A4d", "async_scope", delta=0.0, P=SLOW)
    assert tr["outcome"] == "409_rv"
    assert row["decider"] == "rv_precondition" and row["conflict_kind"] == "resourceVersion"
    assert row["primary_class"] == "CORRECT_ABORT" == tr["truth_primary"]
    assert row["secondary_class"] == "CORRECT_ABORT"
    assert int(row["conflict_current_rv"]) >= int(row["first_write_rv"]) > int(row["precondition_rv"])


def test_a4d_409_from_kubelet_status_update_in_no_write_is_false_abort():
    row, tr, *_ = kit.one("A", "A4d", "no_write", P={"force_status_update_at": 0.05, "d_commit": 2.0})
    assert row["decider"] == "rv_precondition"
    assert row["primary_class"] == "FALSE_ABORT" == tr["truth_primary"]
    assert "conflict_from_other_pod_update" in row["flags"]


def test_a4d_409_from_status_update_before_invalidating_write_is_false_abort():
    # status update at +0.05 ms makes the precondition fail; the writer commits AFTER the check
    row, tr, *_ = kit.one("A", "A4d", "async_scope", delta=20.0, P={"force_status_update_at": 0.05, "d_commit": 2.0})
    assert row["decider"] == "rv_precondition"
    assert row["primary_class"] == "FALSE_ABORT" == tr["truth_primary"]


def test_a4d_accepted_delete_with_late_writer_404_is_valid():
    row, tr, *_ = kit.one("A", "A4d", "async_scope", delta=50.0)
    assert row["primary_class"] == "VALID_EXECUTED" == tr["truth_primary"]
    assert row["primary_reason"] == "write_not_committed_pod_gone(404)" and "writer_404_pod_gone" in row["flags"]


def test_a4d_writer_404_without_accepted_delete_is_infra_error():
    row, tr, raw, audit, wh = kit.one("A", "A4d", "async_scope", delta=50.0)
    raw["delete"].update(dispatched=False, http_status=None)
    raw["gate"]["decision"] = "ABORT_REPLAN"
    assert kit.classify(raw, audit, wh)["primary_reason"] == "writer_404_but_action_not_accepted"


def test_a4d_409_with_unparseable_message_is_unknown_primary_but_secondary_classified():
    row, tr, raw, audit, wh = kit.one("A", "A4d", "async_scope", delta=0.0, P=SLOW)
    for ev in audit:
        if ev["auditID"] == raw["delete"]["audit_id"]:
            ev["responseStatus"]["message"] = "conflict"
            ev["responseObject"]["message"] = "conflict"
    r = kit.classify(raw, audit, wh)
    assert r["primary_class"] == "UNKNOWN" and r["primary_reason"] == "precondition_current_rv_unavailable"
    assert r["secondary_class"] == "CORRECT_ABORT"


def test_a4d_gate_reads_pod_annotations_and_aborts_on_prior_write():
    # writer committed before the gate's Pod read -> the gate sees scope=not allowed -> ABORT_REPLAN
    row, tr, raw, audit, wh = kit.one("A", "A4d", "async_scope", delta=0.0, P=SLOW)
    raw2 = copy.deepcopy(raw)
    raw2["gate"]["decision"] = "ABORT_REPLAN"
    raw2["delete"].update(dispatched=False, http_status=None)
    raw2["bound_state"]["pod_rv_at_gate"] = raw["writer"]["resulting_resource_version"]
    raw2["bound_state"]["annotations_at_gate"]["e38.scope"] = "not allowed"
    r = kit.classify(raw2, audit, wh)
    assert r["decider"] == "gate" and r["primary_class"] == "CORRECT_ABORT"


def test_a4d_gate_read_value_inconsistent_with_rv_is_unknown():
    row, tr, raw, audit, wh = kit.one("A", "A4d", "async_scope", delta=0.0, P=SLOW)
    raw["gate"]["decision"] = "ABORT_REPLAN"
    raw["delete"].update(dispatched=False, http_status=None)
    raw["bound_state"]["annotations_at_gate"]["e38.scope"] = "not allowed"   # value post-write, RV pre-write
    r = kit.classify(raw, audit, wh)
    assert r["primary_reason"] == "decider_read_inconsistent(rv_vs_value)"


# ----------------------------------------------------------------------------- A4m: mirror
def test_a4m_mirror_lag_longer_than_window_executes_invalid():
    row, tr, *_ = kit.one("A", "A4m", "async_scope", delta=0.0, P=dict(SLOW, mirror_lag_fixed=80.0))
    assert row["primary_class"] == "INVALID_EXECUTED" == tr["truth_primary"]
    assert "mirror_lag_exposure" in row["flags"] and row["mirror_before_action"] == 0
    assert row["mirror_lag_ms"] == pytest.approx(80.0, abs=0.01) and row["mirror_lag_rv"] >= 1


def test_a4m_short_mirror_lag_yields_409_correct_abort():
    row, tr, *_ = kit.one("A", "A4m", "async_scope", delta=0.0, P=dict(SLOW, mirror_lag_fixed=0.5))
    assert row["decider"] == "rv_precondition" and row["primary_class"] == "CORRECT_ABORT" == tr["truth_primary"]


def test_a4m_missing_mirror_record_on_409_path_is_unknown():
    row, tr, raw, audit, wh = kit.one("A", "A4m", "async_scope", delta=0.0, P=dict(SLOW, mirror_lag_fixed=0.5))
    raw["mirror"] = None
    assert kit.classify(raw, audit, wh)["primary_reason"] == "mirror_record_missing"


def test_a4m_not_mirrored_write_on_accept_is_judged_by_source_commit():
    row, tr, raw, audit, wh = kit.one("A", "A4m", "async_scope", delta=0.0, P=dict(SLOW, mirror_lag_fixed=80.0))
    raw["mirror"]["mirror_commit_rv"] = None
    r = kit.classify(raw, audit, wh)
    assert r["primary_class"] == "INVALID_EXECUTED" and "mirror_not_mirrored" in r["flags"]


def test_a4m_mirror_rv_not_after_source_is_unknown():
    row, tr, raw, audit, wh = kit.one("A", "A4m", "async_scope", delta=0.0, P=dict(SLOW, mirror_lag_fixed=80.0))
    raw["mirror"]["mirror_commit_rv"] = str(int(raw["mirror"]["source_commit_rv"]) - 1)
    raw["mirror"]["mirror_audit_id"] = None
    assert kit.classify(raw, audit, wh)["primary_reason"] == "mirror_rv_not_after_source"


# ----------------------------------------------------------------------------- E39-B: Poisson arrivals
@pytest.mark.parametrize("arm", ["A1", "A2", "A3", "A4d"])
def test_poisson_multi_arrival_oracle_matches_truth(arm):
    ctx = syn.Ctx(seed=11)
    rng = random.Random(11)
    multi = 0
    for i in range(60):
        raw, audit, wh, tr = syn.simulate_trial(ctx, kit.entry("B", arm, "async_scope", lam=100.0, trial_id=i + 1), rng, {"d_commit": 30.0})
        r = kit.classify(raw, audit, wh)
        assert r["primary_class"] == tr["truth_primary"], (i, r["primary_reason"], tr)
        if r["n_writes_before_action"] not in ("", 0) and int(r["n_writes_before_action"]) >= 2:
            multi += 1
    if arm in ("A1", "A2"):
        assert multi >= 1        # the state at commit is built from SEVERAL arrivals


def test_poisson_arrival_values_default_to_design_value_and_audit_is_checked():
    row, tr, raw, audit, wh = kit.one("B", "A1", "async_scope", lam=100.0, seed=5)
    assert raw["writer_arrivals"] and "new_value" not in raw["writer_arrivals"][0]
    for ev in audit:
        if ev["auditID"] == raw["writer_arrivals"][0]["audit_id"]:
            ev["responseObject"]["data"] = {"allowed": "true"}
    r = kit.classify(raw, audit, wh)
    assert r["primary_reason"].startswith("writer_audit_value_mismatch")


def test_poisson_arrival_server_error_is_infra_error():
    row, tr, raw, audit, wh = kit.one("B", "A1", "async_scope", lam=100.0, seed=5)
    raw["writer_arrivals"][0]["http_status"] = 500
    assert kit.classify(raw, audit, wh)["primary_class"] == "INFRA_ERROR"


def test_poisson_zero_arrivals_is_valid():
    row, tr, raw, audit, wh = kit.one("B", "A1", "async_scope", lam=1.0, seed=3)
    raw["writer_arrivals"] = []
    r = kit.classify(raw, audit, wh)
    assert r["primary_class"] == "VALID_EXECUTED" and r["n_writes"] == 0


# ----------------------------------------------------------------------------- E39-E: NetworkPolicy CREATE
def test_netpol_create_after_invalidating_write_is_invalid():
    row, tr, raw, *_ = kit.one("E", "A1", "async_scope", delta=0.0, P=SLOW)
    assert raw["action_type"] == "netpol_create" and raw["netpol"]["http_status"] == 201
    assert row["primary_class"] == "INVALID_EXECUTED" == tr["truth_primary"]


def test_netpol_create_before_write_is_valid():
    row, tr, *_ = kit.one("E", "A1", "async_evidence_last_literal", delta=50.0)
    assert row["primary_class"] == "VALID_EXECUTED" == tr["truth_primary"]


def test_netpol_webhook_deny_joined_by_object_name():
    row, tr, *_ = kit.one("E", "A3", "async_scope", delta=0.0)
    assert row["primary_class"] == "CORRECT_ABORT" == tr["truth_primary"]
    assert "webhook_joined_by_object_name" in row["flags"]


def test_netpol_create_conflict_is_unknown():
    row, tr, raw, audit, wh = kit.one("E", "A1", "async_scope", delta=50.0)
    raw["netpol"]["http_status"] = 409
    for ev in audit:
        if ev["auditID"] == raw["netpol"]["audit_id"]:
            ev["responseStatus"]["code"] = 409
    assert kit.classify(raw, audit, wh)["primary_reason"] == "create_conflict_409"


def test_a4_arm_with_create_action_is_unknown():
    row, tr, raw, audit, wh = kit.one("E", "A1", "async_scope", delta=50.0)
    raw["arm"] = "A4d_commit_direct"
    assert kit.classify(raw, audit, wh)["primary_reason"] == "a4_not_applicable_to_create"


# ----------------------------------------------------------------------------- E39-G: out-of-model cut
def test_g_out_of_model_cut_dcji_executes_invalid_fullrefresh_aborts():
    r1, t1, *_ = kit.one("G", "A1", "out_of_model_cut_bdf")
    r2, t2, *_ = kit.one("G", "A2", "out_of_model_cut_bdf")
    assert r1["primary_class"] == "INVALID_EXECUTED" == t1["truth_primary"]
    assert r2["primary_class"] == "CORRECT_ABORT" == t2["truth_primary"]
    assert r1["n_writes_committed"] == 3


def test_g_in_model_control_both_abort():
    for arm in ("A1", "A2"):
        r, t, *_ = kit.one("G", arm, "in_model_cut_ace")
        assert r["primary_class"] == "CORRECT_ABORT" == t["truth_primary"]


def test_g_cut_recorded_in_fixture_state_is_accepted():
    r, t, raw, audit, wh = kit.one("G", "A1", "out_of_model_cut_bdf")
    raw["writer_arrivals"] = None
    for o in ("evidence-b", "evidence-d", "evidence-f"):
        raw["fixture"]["initial_values"][o] = "false"
    r2 = kit.classify(raw, audit, wh)
    assert r2["primary_class"] == "INVALID_EXECUTED" and "pre_gate_cut_in_fixture" in r2["flags"]


def test_g_wrong_cut_objects_is_unknown():
    r, t, raw, audit, wh = kit.one("G", "A1", "out_of_model_cut_bdf")
    raw["writer_arrivals"] = raw["writer_arrivals"][:2]
    assert kit.classify(raw, audit, wh)["primary_reason"].startswith("cut_objects_mismatch")


# ----------------------------------------------------------------------------- D, C, F descriptive fields
def test_load_guard_throttled_trial_records_effective_moderate():
    row, tr, raw, *_ = kit.one("D", "A1", "async_scope", delta=5.0, load_level="high", P={"p_throttle": 1.0})
    assert raw["host"]["load_throttled"] is True
    assert row["load_throttled"] == 1 and row["load_level"] == "high" and row["load_level_effective"] == "moderate"
    assert row["primary_class"] == tr["truth_primary"]


def test_c_end_to_end_latency_includes_planning():
    row, tr, raw, *_ = kit.one("C", "A1", "stable", injected_latency_ms=50.0, acquisition_mode="sequential",
                               planning_mode="per_incident", pair_id=1)
    assert row["planning_ms"] == pytest.approx(raw["planning_ms"])
    assert row["e2e_ms"] == pytest.approx(row["planning_ms"] + (raw["delete"]["t_send_mono_ns"] - raw["gate"]["t_start_mono_ns"]) / 1e6)
    assert row["refresh_ms"] > 4 * 50.0 and row["primary_class"] == "VALID_EXECUTED"


def test_f_client_go_row_fields():
    row, tr, raw, *_ = kit.one("F", "A1", "async_scope", impl="client-go", delta=10.0)
    assert row["arm_c"] == "A1" and row["client_impl"] == "client-go" and row["arm"] == "A1_dcji_go"
    assert row["gate_end_to_send_ms"] == pytest.approx(0.02, abs=1e-6)


# ----------------------------------------------------------------------------- generic UNKNOWN / INFRA paths
def test_schema_mismatch_unknown():
    row, tr, raw, audit, wh = kit.one("A", "A1", "async_scope", delta=50.0)
    raw["schema"] = "e38v2.trial.v1"
    assert kit.classify(raw, audit, wh)["primary_reason"].startswith("schema_mismatch")


def test_rv_tie_unknown():
    row, tr, raw, audit, wh = kit.one("A", "A1", "async_scope", delta=0.0, P=SLOW)
    d_aid = raw["delete"]["audit_id"]
    wrv = raw["writer"]["resulting_resource_version"]
    raw["delete"]["deleted_object_resource_version"] = wrv
    for ev in audit:
        if ev["auditID"] == d_aid:
            ev["responseObject"]["metadata"]["resourceVersion"] = wrv
    assert kit.classify(raw, audit, wh)["primary_reason"] == "rv_tie"


def test_audit_vs_trial_rv_conflict_unknown():
    row, tr, raw, audit, wh = kit.one("A", "A1", "async_scope", delta=0.0, P=SLOW)
    raw["writer"]["resulting_resource_version"] = "1"
    assert kit.classify(raw, audit, wh)["primary_reason"].startswith("writer_rv_conflict_audit")


def test_runner_infra_error_and_reset_failure():
    row, tr, raw, audit, wh = kit.one("A", "A1", "no_write")
    r = copy.deepcopy(raw)
    r["status"] = "INFRA_ERROR"
    assert kit.classify(r, audit, wh)["primary_class"] == "INFRA_ERROR"
    r = copy.deepcopy(raw)
    r["fixture"]["reset_ok"] = False
    assert kit.classify(r, audit, wh)["primary_reason"] == "fixture_reset_failed"


def test_unknown_arm_and_experiment():
    row, tr, raw, audit, wh = kit.one("A", "A1", "no_write")
    r = copy.deepcopy(raw)
    r["arm"] = "A9"
    assert kit.classify(r, audit, wh)["primary_reason"] == "unknown_arm(A9)"
    r = copy.deepcopy(raw)
    r["experiment"] = "Z"
    assert kit.classify(r, audit, wh)["primary_reason"].startswith("unknown_experiment")


def test_pod_watch_fallback_for_missing_delete_rv():
    row, tr, raw, audit, wh = kit.one("A", "A1", "async_scope", delta=0.0, P=SLOW)
    rv = raw["delete"]["deleted_object_resource_version"]
    raw["delete"]["deleted_object_resource_version"] = None
    for ev in audit:
        if ev["auditID"] == raw["delete"]["audit_id"]:
            del ev["responseObject"]["metadata"]["resourceVersion"]
    pe = [{"uid": raw["delete"]["expected_uid"], "type": "DELETED", "rv": rv}]
    r = kit.classify(raw, audit, wh, pe)
    assert r["primary_class"] == "INVALID_EXECUTED" and "delete_rv_from_pod_watch" in r["flags"]
    assert kit.classify(raw, audit, wh)["primary_reason"] == "action_rv_missing"


def test_random_mixture_oracle_equals_simulator_truth(mini_run):
    import oracle_e39 as orc
    rows = orc.run_oracle(mini_run["raws"], mini_run["audit"], mini_run["webhook"])
    truth = {t["trial_id"]: t["truth_primary"] for t in mini_run["truth"]}
    bad = [(r["trial_id"], r["primary_class"], truth[r["trial_id"]]) for r in rows if r["primary_class"] != truth[r["trial_id"]]]
    assert not bad and len(rows) == len(truth)


# ----------------------------------------------------------------------------- runner recording conventions
def test_g_fixture_recorded_post_cut_with_cut_writes():
    r, t, raw, *_ = kit.one("G", "A1", "out_of_model_cut_bdf")
    assert raw["fixture"]["initial_values"]["evidence-b"] == "false"
    assert "fixture_state_recorded_post_cut" in r["flags"] and r["primary_class"] == "INVALID_EXECUTED"


def test_g_single_multi_key_pod_annotation_cut_write():
    r, t, raw, audit, wh = kit.one("G", "A1", "out_of_model_cut_bdf")
    first = raw["writer_arrivals"][0]
    raw["writer_arrivals"] = [dict(first, object="pod-annotations:e38.evidence/b,e38.evidence/d,e38.evidence/f",
                                   audit_id=None)]
    r2 = kit.classify(raw, audit, wh)
    assert r2["primary_class"] == "INVALID_EXECUTED" and r2["n_writes_committed"] == 3


def test_noop_patch_same_rv_arrivals_are_one_commit():
    for seed in range(1, 40):
        r, t, raw, audit, wh = kit.one("B", "A1", "async_scope", lam=100.0, seed=seed, P={"d_commit": 30.0})
        arr = [a for a in raw["writer_arrivals"] if a["http_status"] == 200]
        if len(arr) >= 2 and r["primary_class"] not in ("UNKNOWN",):
            break
    else:
        pytest.skip("no multi-arrival trial")
    rv0 = arr[0]["resulting_resource_version"]
    arr[1]["resulting_resource_version"] = rv0
    for ev in audit:
        if ev["auditID"] == arr[1]["audit_id"]:
            ev["responseObject"]["metadata"]["resourceVersion"] = rv0
    r2 = kit.classify(raw, audit, wh)
    assert "noop_patch_same_rv" in r2["flags"] and r2["primary_class"] == r["primary_class"]


def test_same_rv_different_objects_is_unknown():
    r, t, raw, audit, wh = kit.one("G", "A1", "out_of_model_cut_bdf")
    a = raw["writer_arrivals"]
    a[1]["resulting_resource_version"] = a[0]["resulting_resource_version"]
    a[1]["audit_id"] = None
    assert kit.classify(raw, audit, wh)["primary_reason"] == "writes_share_rv"


def test_a4d_arrival_object_is_pod_name_and_is_inferred():
    r, t, raw, audit, wh = kit.one("B", "A4d", "async_scope", lam=100.0, seed=4, P={"d_commit": 30.0})
    assert raw["writer_arrivals"] and raw["writer_arrivals"][0]["object"] == "e39-target"
    assert r["primary_class"] == t["truth_primary"] and "arrival_object_inferred" in r["flags"]


def test_a4_gate_pod_read_recorded_by_pod_name():
    row, tr, raw, *_ = kit.one("A", "A4d", "async_scope", delta=0.0, P=SLOW)
    assert raw["gate"]["reads"][0]["object"] == "e39-target"
    assert row["secondary_class"] == row["primary_class"] == "CORRECT_ABORT"

def test_c_acquisition_block_from_paired_driver_takes_precedence():
    row, tr, raw, audit, wh = kit.one("C", "A1", "stable", injected_latency_ms=50.0, acquisition_mode="parallel",
                                      planning_mode="per_incident", pair_id=3)
    a_s = raw["gate"]["t_start_mono_ns"] - 5_000_000
    raw["acquisition"] = {"refresh_ms": 12.5, "gate_ms": 20.0, "t_start_mono_ns": a_s, "justified": True}
    r = kit.classify(raw, audit, wh)
    assert r["refresh_ms"] == 12.5 and r["gate_ms"] == 20.0 and r["c_justified"] == 1
    assert r["e2e_ms"] == pytest.approx((raw["delete"]["t_send_mono_ns"] - a_s) / 1e6)
    raw["delete"] = {"dispatched": False}
    raw["gate"]["decision"] = "ABORT_REPLAN"
    r = kit.classify(raw, audit, wh)
    assert r["e2e_ms"] == 20.0 and r["e2e_kind"] == "acquisition_guard_only(incl_planning)"

def test_409_attributed_to_kubelet_status_update_vs_writer():
    row, tr, raw, audit, wh = kit.one("A", "A4d", "no_write", P={"force_status_update_at": 0.05, "d_commit": 2.0})
    y = row["conflict_current_rv"]
    audit.append({"auditID": "kubelet-1", "stage": "ResponseComplete", "verb": "patch",
                  "user": {"username": "system:node:e39-control-plane"},
                  "objectRef": {"resource": "pods", "subresource": "status", "name": "e39-target"},
                  "responseStatus": {"code": 200}, "requestReceivedTimestamp": "2026-09-26T06:00:00.000100Z",
                  "stageTimestamp": "2026-09-26T06:00:00.000200Z",
                  "responseObject": {"kind": "Pod", "metadata": {"resourceVersion": str(y)}}})
    r = kit.classify(raw, audit, wh)
    assert r["primary_class"] == "FALSE_ABORT" and r["conflict_attribution"] == "kubelet_status_update"
    r2, *_ = kit.one("A", "A4d", "async_scope", delta=0.0, P=SLOW)
    assert r2["primary_class"] == "CORRECT_ABORT" and r2["conflict_attribution"] == "writer"