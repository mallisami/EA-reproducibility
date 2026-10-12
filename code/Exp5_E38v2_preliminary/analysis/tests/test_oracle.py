"""Oracle classification tests (synthetic, offline)."""
import copy
import itertools

import pytest

import oracle as orc
import synth
from e38v2_common import POLICY_OBJECTS, norm_object, parse_bool, parse_k8s_time_ns, v_core, format_k8s_time_us


def classify(t, a, w):
    return orc.classify_trial(t, orc.index_audit(a), orc.index_webhook(w))


def audit_of(a, aid, stage="ResponseComplete"):
    return [e for e in a if e["auditID"] == aid and e["stage"] == stage][0]


# ----------------------------------------------------------------------------- policy
def test_vcore_truth_table_all_256():
    for bits in itertools.product([False, True], repeat=8):
        st = dict(zip(POLICY_OBJECTS, bits))
        a, b, c, d, e, f, scope, app = bits
        want = ((a and b) or (c and d) or (e and f)) and scope and app
        assert v_core(st) == (want, [])


def test_vcore_missing_object_reported():
    st = {o: True for o in POLICY_OBJECTS}
    del st["evidence-d"]
    assert v_core(st) == (None, ["evidence-d"])


@pytest.mark.parametrize("s,want", [("true", True), ("False", False), ("not allowed", False), ("allowed", True),
                                    (" TRUE ", True), ("0", False), ("maybe", None), (None, None), ("", None)])
def test_parse_bool(s, want):
    assert parse_bool(s) is want


def test_norm_object_aliases():
    assert norm_object("a") == "evidence-a"
    assert norm_object("configmaps/response-scope") == "response-scope"
    assert norm_object("app_policy") == "application-policy"
    assert norm_object("scope") == "response-scope"


def test_k8s_time_parsing():
    assert parse_k8s_time_ns("1970-01-01T00:00:01.000001Z") == 1_000_001_000
    assert parse_k8s_time_ns("1970-01-01T00:00:01.123456789Z") == 1_123_456_789
    assert parse_k8s_time_ns("1970-01-01T01:00:00+01:00") == 0
    assert parse_k8s_time_ns("garbage") is None
    ns = 1_790_402_400_123_456_000
    assert parse_k8s_time_ns(format_k8s_time_us(ns)) == ns


# ----------------------------------------------------------------------------- edge cases from the simulator
@pytest.mark.parametrize("name", sorted(synth.EDGE_CASES))
def test_edge_case_scenarios(name):
    arm, cond, d, P, ep, es = synth.EDGE_CASES[name]
    t, a, w, truth = synth.scenario(arm, cond, d, P=P)
    r = classify(t, a, w)
    assert r["primary_class"] == ep, (r["primary_reason"], r)
    assert r["secondary_class"] == es, (r["secondary_reason"], r)
    assert truth["truth_primary"] == ep


def test_just_before_vs_just_after_rv_margin():
    arm, cond, d, P, _, _ = synth.EDGE_CASES["write_just_before_delete_commit"]
    r1 = classify(*synth.scenario(arm, cond, d, P=P)[:3])
    arm, cond, d, P, _, _ = synth.EDGE_CASES["write_just_after_delete_commit"]
    r2 = classify(*synth.scenario(arm, cond, d, P=P)[:3])
    assert r1["rv_margin"] > 0 and r2["rv_margin"] < 0
    assert r1["writer_rv_source"] == "audit+trial" and r1["delete_rv_source"] == "audit+trial"
    assert r1["rules_agree"] == 0 and r2["rules_agree"] == 1


def test_fence_reason():
    r = classify(*synth.scenario("A1_dcji", "async_scope", -2.0)[:3])
    assert r["primary_reason"] == "gate_unknown_coherence"


def test_equal_timestamps_secondary_unknown_primary_decides():
    t, a, w, _ = synth.scenario("A0_native_uid", "async_scope", -1.0)
    rr = audit_of(a, t["delete"]["audit_id"])["requestReceivedTimestamp"]
    audit_of(a, t["writer"]["audit_id"])["stageTimestamp"] = rr
    r = classify(t, a, w)
    assert r["primary_class"] == "INVALID_EXECUTED"
    assert r["secondary_class"] == "UNKNOWN" and r["secondary_reason"] == "time_tie"
    assert r["rules_agree"] == "NA"


def test_missing_writer_audit_falls_back_to_trial_rv():
    t, a, w, _ = synth.scenario("A0_native_uid", "async_scope", -1.0)
    a = [e for e in a if e["auditID"] != t["writer"]["audit_id"]]
    r = classify(t, a, w)
    assert r["primary_class"] == "INVALID_EXECUTED" and r["writer_rv_source"] == "trial"
    assert "audit_missing_writer" in r["flags"]
    assert r["secondary_class"] == "UNKNOWN" and r["secondary_reason"] == "time_rule_writer_audit_missing"


def test_missing_delete_audit_and_trial_rv_is_unknown():
    t, a, w, _ = synth.scenario("A0_native_uid", "async_scope", -1.0)
    a = [e for e in a if e["auditID"] != t["delete"]["audit_id"]]
    t["delete"]["deleted_object_resource_version"] = None
    r = classify(t, a, w)
    assert r["primary_class"] == "UNKNOWN" and r["primary_reason"] == "delete_rv_missing"
    assert r["secondary_class"] == "UNKNOWN"


def test_rv_conflict_between_audit_and_trial_is_unknown():
    t, a, w, _ = synth.scenario("A0_native_uid", "async_scope", -1.0)
    t["writer"]["resulting_resource_version"] = str(int(t["writer"]["resulting_resource_version"]) + 1)
    r = classify(t, a, w)
    assert r["primary_class"] == "UNKNOWN" and r["primary_reason"].startswith("writer_rv_conflict")


def test_non_numeric_rv_is_unknown():
    t, a, w, _ = synth.scenario("A0_native_uid", "async_scope", -1.0)
    a = [e for e in a if e["auditID"] != t["writer"]["audit_id"]]
    t["writer"]["resulting_resource_version"] = "abc"
    r = classify(t, a, w)
    assert r["primary_class"] == "UNKNOWN" and "rv_not_numeric" in r["primary_reason"]


def test_rv_tie_is_unknown():
    t, a, w, _ = synth.scenario("A0_native_uid", "async_scope", -1.0)
    a = [e for e in a if e["auditID"] not in (t["writer"]["audit_id"], t["delete"]["audit_id"])]
    t["writer"]["resulting_resource_version"] = "777"
    t["delete"]["deleted_object_resource_version"] = "777"
    r = classify(t, a, w)
    assert r["primary_class"] == "UNKNOWN" and r["primary_reason"] == "rv_tie"


def test_delete_audit_status_mismatch_is_unknown():
    t, a, w, _ = synth.scenario("A1_dcji", "no_write", None)
    audit_of(a, t["delete"]["audit_id"])["responseStatus"]["code"] = 500
    r = classify(t, a, w)
    assert r["primary_class"] == "UNKNOWN" and r["primary_reason"].startswith("delete_audit_status_mismatch")


def test_uid_409_blocked_all_arms():
    for arm in ("A0_native_uid", "A1_dcji", "A2_fullrefresh", "A3_dcji_webhook"):
        r = classify(*synth.scenario(arm, "uid_rebind_control", None)[:3])
        assert r["primary_class"] == "UID_BLOCKED" and r["secondary_class"] == "UID_BLOCKED"


def test_409_in_async_condition_is_uid_blocked():
    t, a, w, _ = synth.scenario("A1_dcji", "async_scope", 50.0)
    t["delete"]["http_status"] = 409
    audit_of(a, t["delete"]["audit_id"])["responseStatus"]["code"] = 409
    assert classify(t, a, w)["primary_class"] == "UID_BLOCKED"


def test_uid_rebind_accepted_wrong_object_is_invalid():
    t, a, w, _ = synth.scenario("A1_dcji", "no_write", None)
    t["condition"] = "uid_rebind_control"
    ev = audit_of(a, t["delete"]["audit_id"])
    ev["responseObject"]["metadata"]["uid"] = "someone-else"
    r = classify(t, a, w)
    assert r["primary_class"] == "INVALID_EXECUTED" and r["primary_reason"] == "uid_rebind_wrong_object_deleted"


def _webhook_deny(t, a, w):
    for e in w:
        e["decision"] = "deny"
    t["delete"].update(http_status=403, deleted_object_resource_version=None)
    ev = audit_of(a, t["delete"]["audit_id"])
    ev["responseStatus"]["code"] = 403
    ev["responseObject"] = synth.status_object(403, "Forbidden", "denied")


def test_webhook_deny_valid_state_no_write_is_false_abort():
    t, a, w, _ = synth.scenario("A3_dcji_webhook", "no_write", None)
    _webhook_deny(t, a, w)
    r = classify(t, a, w)
    assert r["primary_class"] == "FALSE_ABORT" and r["decider"] == "webhook"


def test_webhook_deny_before_write_is_false_abort():
    # write at +50 ms lands long after the webhook read; a deny here is a false abort
    t, a, w, _ = synth.scenario("A3_dcji_webhook", "async_scope", 50.0)
    _webhook_deny(t, a, w)
    r = classify(t, a, w)
    assert r["primary_class"] == "FALSE_ABORT" and r["secondary_class"] == "FALSE_ABORT"


def test_403_without_webhook_log_is_unknown():
    t, a, w, _ = synth.scenario("A3_dcji_webhook", "no_write", None)
    _webhook_deny(t, a, w)
    r = classify(t, a, [])
    assert r["primary_class"] == "UNKNOWN" and "webhook_log_missing" in r["primary_reason"]
    t2, a2, w2, _ = synth.scenario("A1_dcji", "no_write", None)
    t2["delete"]["http_status"] = 403
    audit_of(a2, t2["delete"]["audit_id"])["responseStatus"]["code"] = 403
    assert classify(t2, a2, w2)["primary_reason"] == "delete_denied_403_unattributed"


def test_webhook_deny_but_accepted_is_unknown():
    t, a, w, _ = synth.scenario("A3_dcji_webhook", "no_write", None)
    for e in w:
        e["decision"] = "deny"
    assert classify(t, a, w)["primary_reason"] == "webhook_deny_but_delete_accepted"


def test_gate_abort_without_write_seen_is_false_abort():
    t, a, w, _ = synth.scenario("A1_dcji", "async_scope", 20.0)
    t["gate"]["decision"] = "ABORT_REPLAN"
    t["delete"]["dispatched"] = False
    r = classify(t, a, w)
    assert r["primary_class"] == "FALSE_ABORT" and r["secondary_class"] == "FALSE_ABORT"


def test_gate_abort_no_write_is_false_abort():
    t, a, w, _ = synth.scenario("A2_fullrefresh", "no_write", None)
    t["gate"]["decision"] = "ABORT_REPLAN"
    t["delete"]["dispatched"] = False
    assert classify(t, a, w)["primary_class"] == "FALSE_ABORT"


def test_decider_read_inconsistent_is_unknown():
    t, a, w, _ = synth.scenario("A1_dcji", "async_scope", -5.0)
    for rd in t["gate"]["reads"]:
        if rd["object"] == "response-scope":
            rd["value"] = "true"          # rv says post-write, value says pre-write
    r = classify(t, a, w)
    assert r["primary_class"] == "UNKNOWN" and r["primary_reason"].startswith("decider_read_inconsistent")


def test_infra_and_status_paths():
    t, a, w, _ = synth.scenario("A1_dcji", "async_scope", 1.0)
    t1 = copy.deepcopy(t); t1["status"] = "INFRA_ERROR"
    assert classify(t1, a, w)["primary_class"] == "INFRA_ERROR"
    t2 = copy.deepcopy(t); t2["fixture"]["reset_ok"] = False
    assert classify(t2, a, w)["primary_reason"] == "fixture_reset_failed"
    t3 = copy.deepcopy(t); t3["writer"]["http_status"] = 500
    assert classify(t3, a, w)["primary_class"] == "INFRA_ERROR"
    t4 = copy.deepcopy(t); t4["status"] = "UNKNOWN"
    assert classify(t4, a, w)["primary_class"] == "UNKNOWN"
    t5 = copy.deepcopy(t); t5["schema"] = "e38.trial.v0"
    assert classify(t5, a, w)["primary_reason"].startswith("schema_mismatch")


def test_fixture_problems_are_unknown():
    t, a, w, _ = synth.scenario("A1_dcji", "async_evidence_last_literal", 1.0)
    t1 = copy.deepcopy(t); t1["fixture"]["initial_values"]["evidence-c"] = "true"
    assert classify(t1, a, w)["primary_reason"].startswith("fixture_condition_mismatch")
    t2 = copy.deepcopy(t); del t2["fixture"]["initial_values"]["evidence-b"]
    assert classify(t2, a, w)["primary_reason"].startswith("initial_state_incomplete")
    t3 = copy.deepcopy(t); t3["fixture"]["initial_values"]["response-scope"] = "false"
    assert classify(t3, a, w)["primary_reason"] == "fixture_prewrite_vcore_false"
    t4 = copy.deepcopy(t); t4["writer"]["object"] = "evidence-b"
    assert classify(t4, a, w)["primary_reason"].startswith("writer_object_mismatch")


def test_writer_audit_value_mismatch_is_unknown():
    t, a, w, _ = synth.scenario("A1_dcji", "async_app_policy", 1.0)
    audit_of(a, t["writer"]["audit_id"])["responseObject"]["data"]["allowed"] = "true"
    assert classify(t, a, w)["primary_reason"].startswith("writer_audit_value_mismatch")


def test_not_dispatched_without_abort_is_unknown():
    t, a, w, _ = synth.scenario("A1_dcji", "no_write", None)
    t["delete"]["dispatched"] = False
    assert classify(t, a, w)["primary_reason"].startswith("not_dispatched_without_abort")


def test_webhook_joined_by_pod_uid():
    # live runner: webhook_request_uid is null; the entry is joined on pod_uid == delete.expected_uid
    t, a, w, _ = synth.scenario("A3_dcji_webhook", "async_scope", -1.0)
    assert t["webhook_request_uid"] is None and len(w) == 1
    r = classify(t, a, w)
    assert r["decider"] == "webhook" and r["primary_class"] == "CORRECT_ABORT"
    assert "webhook_joined_by_pod_uid" in r["flags"] and r["webhook_request_uid"] == w[0]["request_uid"]
    # an entry with a different pod uid is not joined -> denial unattributable -> UNKNOWN
    w2 = copy.deepcopy(w)
    w2[0]["pod_uid"] = "other-pod"
    r2 = classify(t, a, w2)
    assert r2["primary_class"] == "UNKNOWN" and "webhook_log_missing" in r2["primary_reason"]
    # an explicit request uid on the trial still takes precedence
    t3 = copy.deepcopy(t)
    t3["webhook_request_uid"] = w[0]["request_uid"]
    assert classify(t3, a, w2)["primary_class"] == "CORRECT_ABORT"


def test_webhook_multiple_invocations_governing_entry():
    # accepted DELETE whose later (stale-copy) re-evaluation denied: the apiserver discarded that deny;
    # the earliest evaluated ALLOW governs; logged not-evaluated terminating calls are ignored
    t, a, w, _ = synth.scenario("A3_dcji_webhook", "async_scope", 50.0)
    base = copy.deepcopy(w[0])
    assert base["decision"] == "allow"
    stale = copy.deepcopy(base)
    stale.update(request_uid="stale-1", decision="deny", received_wall_ns=base["received_wall_ns"] + 20_000_000,
                 completed_wall_ns=base["completed_wall_ns"] + 20_000_000)
    term = dict(base, request_uid="term-1", decision="allow", reads=[], pod_terminating=True,
                received_wall_ns=base["received_wall_ns"] + 40_000_000)
    r = classify(t, a, [base, stale, term])
    assert r["primary_class"] == "VALID_EXECUTED" and r["webhook_request_uid"] == base["request_uid"]
    assert "webhook_multiple_evaluations(2)" in r["flags"] and "webhook_evaluations_differ" in r["flags"]
    assert "webhook_terminating_calls(1)" in r["flags"]
    # only denies but accepted -> still UNKNOWN
    r2 = classify(t, a, [dict(base, decision="deny"), stale])
    assert r2["primary_reason"] == "webhook_deny_but_delete_accepted"
    # denied DELETE with two denies -> the LAST deny (fresh copy) governs
    t3, a3, w3, _ = synth.scenario("A3_dcji_webhook", "async_scope", -1.0)
    first = dict(copy.deepcopy(w3[0]), request_uid="early", received_wall_ns=w3[0]["received_wall_ns"] - 5_000_000)
    r3 = classify(t3, a3, [first, w3[0]])
    assert r3["decider"] == "webhook" and r3["webhook_request_uid"] == w3[0]["request_uid"]
    assert r3["primary_class"] == "CORRECT_ABORT"


def test_webhook_entry_on_non_webhook_arm_is_flagged():
    t, a, w, _ = synth.scenario("A3_dcji_webhook", "no_write", None)
    t["arm"] = "A1_dcji"
    r = classify(t, a, w)
    assert "webhook_entry_for_non_webhook_arm" in r["flags"]


def test_webhook_time_rule_uses_per_read_send_time_else_fallback():
    t, a, w, _ = synth.scenario("A3_dcji_webhook", "async_scope", 0.5)
    r = classify(t, a, w)
    assert r["ref_ts_kind"] == "webhook_read_send_wall_ns"
    scope_read = [x for x in w[0]["reads"] if x["object"] == "response-scope"][0]
    assert r["ref_ts_ns"] == scope_read["t_send_wall_ns"]
    assert (r["primary_class"], r["secondary_class"]) == ("CORRECT_ABORT", "CORRECT_ABORT")
    w2 = copy.deepcopy(w)
    for x in w2[0]["reads"]:
        del x["t_send_wall_ns"], x["t_recv_wall_ns"]
    r2 = classify(t, a, w2)
    assert r2["ref_ts_kind"].startswith("webhook_received_wall_ns") and "webhook_read_time_missing" in r2["flags"]
    assert r2["secondary_class"] == "FALSE_ABORT"      # conservative fallback


def test_evidence_columns_present():
    t, a, w, _ = synth.scenario("A1_dcji", "async_scope", 1.0)
    r = classify(t, a, w)
    assert set(orc.CSV_COLS) <= set(r)
    assert abs(r["release_error_us"] - 40.0) < 0.01
    assert abs(r["realized_delta_ms"] - 1.04) < 1e-4
    assert r["delete_rtt_ms"] > 0


def test_edge_case_files_roundtrip(tmp_path):
    out = synth.write_edge_cases(tmp_path / "edge")
    trials, wh, au, errs = orc.load_inputs(out / "RAW_TRIALS.jsonl", out / "webhook_log.jsonl", out / "audit.log")
    assert not any(errs.values())
    exp = {e["trial_id"]: e for e in orc.load_jsonl(out / "EXPECTED.jsonl")[0]}
    rows = orc.run_oracle(trials, au, wh)
    for r in rows:
        e = exp[r["trial_id"]]
        assert (r["primary_class"], r["secondary_class"]) == (e["expected_primary"], e["expected_secondary"]), e["case"]
    orc.write_csv(rows, tmp_path / "o.csv")
    assert (tmp_path / "o.csv").read_text().count("\n") == len(rows) + 1
