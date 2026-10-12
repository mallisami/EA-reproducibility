"""E38v2 shared constants and pure helpers (offline analysis only; no cluster access).

Everything here is pre-registered: the design (arms, conditions, delta grid, stage sizes),
the validity policy J/V_core, the Wilson interval, the stage-2 top-up rule.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path

SPEC_VERSION = "E38v2 interface spec v1 (2026-09-26)"
TRIAL_SCHEMA = "e38v2.trial.v1"
WEBHOOK_SCHEMA = "e38v2.webhook.v1"
SCHEDULE_SCHEMA = "e38v2.schedule.v1"
RESULTS_SCHEMA = "e38v2.results.v1"
POD_EVENT_SCHEMA = "e38v2.podevent.v1"      # E38v2 RERUN (plan v1.2): src/pod_watch.py output
FORBIDDEN_SEED = 20260912
# E38v2 RERUN (plan v1.2): the first E38v2 run's schedule seed is also refused.
FORBIDDEN_SEEDS = (20260912, 1772720255)

# ----------------------------------------------------------------------------- design
ARMS = ("A0_native_uid", "A1_dcji", "A2_fullrefresh", "A3_dcji_webhook")
ASYNC_CONDITIONS = ("async_scope", "async_app_policy", "async_evidence_last_literal")
CONDITIONS = ("no_write",) + ASYNC_CONDITIONS + ("uid_rebind_control",)
DELTAS_MS = (-5.0, -2.0, -1.0, 0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 20.0, 50.0)
N_STAGE1_ASYNC = 39
N_STAGE1_NO_WRITE = 39
N_STAGE1_UID = 10
N_TOPUP_TARGET = 93
TOPUP_LO = 0.05     # strict: p_hat must be > TOPUP_LO
TOPUP_HI = 0.95     # strict: p_hat must be < TOPUP_HI
EXPOSURE_THRESHOLD = 0.05
Z95 = 1.959963984540054

# Object written by the async writer, per condition.
WRITER_OBJECT = {
    "async_scope": "response-scope",
    "async_app_policy": "application-policy",
    "async_evidence_last_literal": "evidence-a",
}

OUTCOMES = ("VALID_EXECUTED", "INVALID_EXECUTED", "CORRECT_ABORT", "FALSE_ABORT",
            "UID_BLOCKED", "UNKNOWN", "INFRA_ERROR")
NOT_ANALYZABLE = ("UNKNOWN", "INFRA_ERROR")


def arm_index(a):
    return ARMS.index(a) if a in ARMS else len(ARMS)


def cond_index(c):
    return CONDITIONS.index(c) if c in CONDITIONS else len(CONDITIONS)


def delta_sort_key(d):
    return (-math.inf if d is None else float(d))


def cell_sort_key(cell):
    arm, cond, delta = cell
    return (arm_index(arm), cond_index(cond), delta_sort_key(delta), str(arm), str(cond))


def norm_delta(d):
    if d is None:
        return None
    return float(d)


def deltas_equal(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) < 1e-9


def delta_label(d):
    return "NA" if d is None else ("%g" % float(d))


def stage1_design_cells():
    """{(arm, condition, delta): planned stage-1 n} of the pre-registered design."""
    cells = {}
    for arm in ARMS:
        cells[(arm, "no_write", None)] = N_STAGE1_NO_WRITE
        for cond in ASYNC_CONDITIONS:
            for d in DELTAS_MS:
                cells[(arm, cond, d)] = N_STAGE1_ASYNC
        cells[(arm, "uid_rebind_control", None)] = N_STAGE1_UID
    return cells


# ----------------------------------------------------------------------------- policy
POLICY_OBJECTS = ("evidence-a", "evidence-b", "evidence-c", "evidence-d", "evidence-e",
                  "evidence-f", "response-scope", "application-policy")
_ALIASES = {
    "a": "evidence-a", "b": "evidence-b", "c": "evidence-c", "d": "evidence-d",
    "e": "evidence-e", "f": "evidence-f",
    "scope": "response-scope", "scope_ok": "response-scope", "response_scope": "response-scope",
    "app_policy": "application-policy", "application_policy": "application-policy",
    "app-policy": "application-policy", "app_policy_allowed": "application-policy",
    "epoch": "e38-epoch", "e38_epoch": "e38-epoch",
}
for _x in "abcdef":
    _ALIASES["evidence_" + _x] = "evidence-" + _x


def norm_object(name):
    """Canonical ConfigMap name. Accepts 'a', 'evidence-a', 'configmaps/evidence-a', 'ns/evidence-a'."""
    if name is None:
        return None
    s = str(name).strip()
    if "/" in s:
        s = s.rsplit("/", 1)[1]
    s_l = s.lower()
    return _ALIASES.get(s_l, s_l)


TRUE_STRINGS = {"true", "allowed", "allow", "yes", "1"}
FALSE_STRINGS = {"false", "not allowed", "not_allowed", "not-allowed", "notallowed",
                 "denied", "deny", "disallowed", "no", "0"}


def parse_bool(v):
    """Strict truth parsing of a ConfigMap value. Returns True/False, or None if unparseable."""
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in TRUE_STRINGS:
        return True
    if s in FALSE_STRINGS:
        return False
    return None


def v_core(state):
    """state: {canonical object: bool}. Returns (value or None, missing list).

    J = (a AND b) OR (c AND d) OR (e AND f); V_core = J AND scope_ok AND app_policy_allowed.
    """
    missing = [o for o in POLICY_OBJECTS if state.get(o) is None]
    if missing:
        return None, missing
    g = lambda x: state["evidence-" + x]
    j = (g("a") and g("b")) or (g("c") and g("d")) or (g("e") and g("f"))
    return bool(j and state["response-scope"] and state["application-policy"]), []


# ----------------------------------------------------------------------------- statistics
def wilson(k, n, z=Z95):
    """Wilson score interval. Returns (p_hat, lo, hi); (None, None, None) when n == 0."""
    if n is None or n <= 0:
        return None, None, None
    if k < 0 or k > n:
        raise ValueError("k out of range")
    p = k / n
    z2 = z * z
    den = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / den
    half = (z / den) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    lo = max(0.0, centre - half)
    hi = min(1.0, centre + half)
    if k == 0:
        lo = 0.0
    if k == n:
        hi = 1.0
    return p, lo, hi


def topup_decision(p_hat):
    """Pre-registered stage-2 rule: top up iff 0.05 < p_hat < 0.95 (strict).
    p_hat None (no analyzable stage-1 trial, e.g. every trial fenced UNKNOWN_COHERENCE) does not satisfy
    the inequality -> NOT topped up; such cells are listed separately in the top-up output."""
    if p_hat is None:
        return False
    return TOPUP_LO < p_hat < TOPUP_HI


def exposure_d_star(deltas, phats, thr=EXPOSURE_THRESHOLD):
    """Smallest grid delta d* such that p_hat <= thr at d* AND at every larger grid delta.
    deltas sorted ascending; phats aligned (None = undefined -> cannot certify closed).
    Returns (d_star or None if not closed within grid, last_exposed_delta or None)."""
    d_star = None
    for i in range(len(deltas) - 1, -1, -1):
        p = phats[i]
        if p is None or p > thr:
            break
        d_star = deltas[i]
    last_exposed = None
    for i in range(len(deltas)):
        p = phats[i]
        if p is not None and p > thr:
            last_exposed = deltas[i]
    return d_star, last_exposed


# ----------------------------------------------------------------------------- IO helpers
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_jsonl(path):
    """Returns (records, errors). errors: list of (line_no, message). Blank lines ignored."""
    recs, errs = [], []
    with open(path, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError as ex:
                errs.append((i, "json: %s" % ex))
                continue
            if not isinstance(obj, dict):
                errs.append((i, "not a JSON object"))
                continue
            recs.append(obj)
    return recs, errs


_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:?\d{2})?$")


def parse_k8s_time_ns(s):
    """RFC3339(Nano) -> integer ns since epoch (UTC). Returns None if absent/unparseable."""
    if s is None:
        return None
    m = _TS_RE.match(str(s).strip())
    if not m:
        return None
    date, clock, frac, tz = m.groups()
    dt = datetime.strptime(date + "T" + clock, "%Y-%m-%dT%H:%M:%S")
    secs = calendar.timegm(dt.timetuple())
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        tzd = tz[1:].replace(":", "")
        secs -= sign * (int(tzd[:2]) * 3600 + int(tzd[2:]) * 60)
    ns = int((frac or "").ljust(9, "0")) if frac else 0
    return secs * 1_000_000_000 + ns


def format_k8s_time_us(ns):
    """ns since epoch -> RFC3339 with microseconds (kube-apiserver audit format)."""
    from datetime import timedelta
    secs, rem = divmod(int(ns), 1_000_000_000)
    dt = datetime(1970, 1, 1) + timedelta(seconds=secs)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + ".%06dZ" % (rem // 1000)


def rv_int(rv):
    """resourceVersion -> int, or None if absent/non-numeric (opaque per API conventions)."""
    if rv is None:
        return None
    s = str(rv).strip()
    if not s.isdigit():
        return None
    return int(s)
