"""Plan 2.3 (f) (pre-LOCK, e39 smoke 2026-09-26): an A4d Pod PATCH answered 2xx with a responseObject carrying
metadata.deletionTimestamp was applied to a Pod already marked for deletion by the accepted DELETE; the
object was removed during that update and the write never persisted -> not committed."""
import copy

import e39_testkit as kit
import recompute_independent_e39 as ind


def _terminating_variant():
    row, tr, raw, audit, wh = kit.one("A", "A4d", "async_scope", delta=50.0)
    assert row["primary_class"] == "VALID_EXECUTED" and "writer_404_pod_gone" in row["flags"]
    raw = copy.deepcopy(raw)
    audit = copy.deepcopy(audit)
    aid = raw["writer"]["audit_id"]
    act_rv = int(row["action_rv"]) if row["action_rv"] != "" else None
    mark_rv = str((act_rv - 1) if act_rv else 999999)
    raw["writer"]["http_status"] = 200
    raw["writer"]["resulting_resource_version"] = mark_rv
    for ev in audit:
        if ev.get("auditID") == aid:
            ev["responseStatus"] = {"metadata": {}, "code": 200}
            ev["responseObject"] = {"kind": "Pod", "apiVersion": "v1",
                                    "metadata": {"name": "e39-target", "resourceVersion": mark_rv,
                                                 "uid": raw["delete"]["expected_uid"],
                                                 "deletionTimestamp": "2026-09-26T14:42:07Z",
                                                 "deletionGracePeriodSeconds": 0}}
    return raw, audit, wh


def test_write_on_terminating_pod_is_not_committed():
    raw, audit, wh = _terminating_variant()
    row = kit.classify(raw, audit, wh)
    assert row["primary_class"] == "VALID_EXECUTED"
    assert "writer_on_terminating_pod" in row["flags"]
    assert row["secondary_class"] == "VALID_EXECUTED"


def test_write_on_terminating_pod_without_accepted_delete_is_unknown():
    raw, audit, wh = _terminating_variant()
    raw["delete"].update(dispatched=False, http_status=None)
    raw["gate"]["decision"] = "ABORT_REPLAN"
    row = kit.classify(raw, audit, wh)
    assert row["primary_class"] == "UNKNOWN"
    assert row["primary_reason"] == "write_on_terminating_pod_but_action_not_accepted"


def test_independent_recompute_same_rule():
    raw, audit, wh = _terminating_variant()
    aud = ind.audit_rc(audit)
    assert ind.classify(raw, aud)[0] == "nonevent"
