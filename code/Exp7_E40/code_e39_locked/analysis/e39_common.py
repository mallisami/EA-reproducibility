"""E39 shared constants and pure helpers (offline analysis only; never contacts a cluster).

Extends analysis/base_e38v2r/e38v2_common.py (E38v2 confirmatory rerun, plan v1.2). Everything here
is pre-registered in protocol/ANALYSIS_PLAN_E39.txt: the design of sub-experiments A-G, the canonical
names, the validity policy J/V_core, Wilson / Newcombe / exact-unconditional intervals, the
Hodges-Lehmann estimators, the stage-2 top-up rule (E39-A only) and the Poisson window model (E39-B).
"""
from __future__ import annotations

import calendar
import hashlib
import json
import math
import re
from datetime import datetime

SPEC_VERSION = "E39 design spec v1 (2026-09-26)"
TRIAL_SCHEMA = "e39.trial.v1"
WEBHOOK_SCHEMAS = ("e39.webhook.v1", "e38v2.webhook.v1")
SCHEDULE_SCHEMA = "e39.schedule.v1"
RESULTS_SCHEMA = "e39.results.v1"
# 20260912 = E38 original; 1772720255 = first E38v2 run; 154080643 = E38v2 confirmatory rerun (its LOCK,
# ~/e38v2r_20260926/protocol/LOCK_E38v2.json, status LOCKED-2026-09-26T10:34:43Z)
FORBIDDEN_SEEDS = (20260912, 1772720255, 154080643)
Z95 = 1.959963984540054

# ----------------------------------------------------------------------------- canonical names
ARMS = ("A0", "A1", "A2", "A3", "A4d", "A4m")
_ARM_ALIASES = {
    "a0": "A0", "a0_native_uid": "A0",
    "a1": "A1", "a1_dcji": "A1", "dcji": "A1",
    "a2": "A2", "a2_fullrefresh": "A2", "fullrefresh": "A2", "a2_full_refresh": "A2",
    "a3": "A3", "a3_dcji_webhook": "A3",
    "a4d": "A4d", "a4d_commit_bound_direct": "A4d", "a4d_direct": "A4d", "a4_direct": "A4d",
    "a4d_commit_direct": "A4d",                                   # E39_RECORD_SCHEMA.json names
    "a4m": "A4m", "a4m_commit_bound_mirror": "A4m", "a4m_mirror": "A4m", "a4m_mirrored": "A4m",
    "a4m_commit_mirror": "A4m",
    "a4_mirror": "A4m",
}
CLIENT_IMPLS = ("python", "client-go")
_GO_SUFFIX = re.compile(r"^(.*?)[-_](go|client-go|clientgo|golang)$", re.I)
RV_PRECONDITION_ARMS = ("A4d", "A4m")
WEBHOOK_ARMS = ("A3",)


def canon_arm(raw, client_impl=None):
    """Raw arm string (+ optional client_impl field) -> (canonical arm, client implementation).
    'A1-go' / 'A1_dcji_go' -> ('A1', 'client-go'). Unknown arm -> (None, impl)."""
    if raw is None:
        return None, None
    s = str(raw).strip()
    impl = None
    m = _GO_SUFFIX.match(s)
    if m:
        s, impl = m.group(1), "client-go"
    ci = canon_client_impl(client_impl)
    if ci is not None:
        impl = ci if impl is None else impl
    return _ARM_ALIASES.get(s.lower()), (impl or "python")


def canon_client_impl(v):
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("python", "py", "python-kubernetes", "python_requests"):
        return "python"
    if s in ("client-go", "go", "clientgo", "golang", "client_go"):
        return "client-go"
    return None


NO_WRITE = "no_write"
UID_REBIND = "uid_rebind_control"
ASYNC_CONDITIONS = ("async_scope", "async_app_policy", "async_evidence_last_literal")
C_CONDITIONS = ("stable", "ea_attack")
G_OUT_OF_MODEL = "out_of_model_cut_bdf"
G_IN_MODEL = "in_model_cut_ace"
G_CONDITIONS = (G_OUT_OF_MODEL, G_IN_MODEL)
CONDITIONS = (NO_WRITE,) + ASYNC_CONDITIONS + (UID_REBIND,) + C_CONDITIONS + G_CONDITIONS
_COND_ALIASES = {
    "poisson_scope": "async_scope", "poisson_async_scope": "async_scope",
    "out_of_model_bdf": G_OUT_OF_MODEL, "in_model_ace": G_IN_MODEL,   # E39_RECORD_SCHEMA.json names
    "oom_cut_bdf": G_OUT_OF_MODEL, "cut_bdf": G_OUT_OF_MODEL, "out_of_model": G_OUT_OF_MODEL,
    "out_of_model_cut": G_OUT_OF_MODEL, "g_out_of_model": G_OUT_OF_MODEL,
    "in_model_control": G_IN_MODEL, "cut_ace": G_IN_MODEL, "in_model_cut": G_IN_MODEL,
    "in_model_control_cut_ace": G_IN_MODEL, "g_in_model": G_IN_MODEL,
    "ea-attack": "ea_attack", "e21_attack": "ea_attack",
}


def canon_condition(raw):
    if raw is None:
        return None
    s = str(raw).strip().lower()
    s = _COND_ALIASES.get(s, s)
    return s if s in CONDITIONS else None


# Object written by the single async writer, per condition (E38v2 semantics).
WRITER_OBJECT = {
    "async_scope": "response-scope",
    "async_app_policy": "application-policy",
    "async_evidence_last_literal": "evidence-a",
}
# E39-G pre-gate cut objects (all set to false before the gate).
G_CUT_OBJECTS = {
    G_OUT_OF_MODEL: ("evidence-b", "evidence-d", "evidence-f"),
    G_IN_MODEL: ("evidence-a", "evidence-c", "evidence-e"),
}

ACTION_TYPES = ("pod_delete", "netpol_create")
LOAD_LEVELS = ("idle", "moderate", "high")
ACQ_MODES = ("sequential", "parallel")
PLANNING_MODES = ("precomputed", "per_incident")
EXPERIMENTS = ("A", "B", "C", "D", "E", "F", "G")


def canon_planning(v):
    if v is None:
        return None
    s = str(v).strip().lower().replace("-", "_")
    return {"per_incident": "per_incident", "perincident": "per_incident", "precomputed": "precomputed",
            "pre_computed": "precomputed"}.get(s)


def canon_acq(v):
    if v is None:
        return None
    s = str(v).strip().lower()
    return {"sequential": "sequential", "seq": "sequential", "parallel": "parallel", "par": "parallel"}.get(s)


def canon_load(v):
    if v is None:
        return None
    s = str(v).strip().lower()
    return s if s in LOAD_LEVELS else None


def canon_action(v):
    if v is None:
        return "pod_delete"
    s = str(v).strip().lower()
    return {"pod_delete": "pod_delete", "delete": "pod_delete", "netpol_create": "netpol_create",
            "networkpolicy_create": "netpol_create", "create_netpol": "netpol_create"}.get(s)


# ----------------------------------------------------------------------------- design (pre-registered)
DELTAS_A = (0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0)   # extended 2026-09-26 (E38v2 rerun: not closed by 50 ms)
DELTAS_SHORT = (0.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0)   # D, E, F (extended 2026-09-26)
LAMBDAS_B = (1.0, 10.0, 100.0)
LAMBDAS_EXTRAPOLATE = (0.01, 0.1)
LATENCIES_C = (0.0, 50.0, 200.0, 1000.0)
N_A_ASYNC = 39
N_A_NO_WRITE = 39
N_A_UID = 10
N_TOPUP_TARGET = 93
TOPUP_LO, TOPUP_HI = 0.05, 0.95
N_B = 400
N_C_PAIRS = 60
N_D = 39
N_E = 39
N_F = 39
N_G = 39
EXPOSURE_THRESHOLD = 0.05
ARMS_A = ("A1", "A3", "A4d", "A4m")
ARMS_B = ("A1", "A2", "A3", "A4d")
ARMS_C = ("A1", "A2")
ARMS_D = ("A1", "A3")
ARMS_E = ("A1", "A3")
# E39-F (added 2026-09-26 by the coordinator, before any LOCK): client-go responder.
ARMS_F = (("A1", "python"), ("A1", "client-go"), ("A4d", "client-go"))
ARMS_G = ("A1", "A2")


def design_cells(experiment):
    """{cell key: planned stage-1 n} for one sub-experiment (see cell_key)."""
    c = {}
    if experiment == "A":
        for arm in ARMS_A:
            c[("A", arm, "python", NO_WRITE, None)] = N_A_NO_WRITE
            c[("A", arm, "python", UID_REBIND, None)] = N_A_UID
            for cond in ASYNC_CONDITIONS:
                for d in DELTAS_A:
                    c[("A", arm, "python", cond, d)] = N_A_ASYNC
    elif experiment == "B":
        for arm in ARMS_B:
            for lam in LAMBDAS_B:
                c[("B", arm, "async_scope", lam)] = N_B
    elif experiment == "C":
        for arm in ARMS_C:
            for lat in LATENCIES_C:
                for acq in ACQ_MODES:
                    for cond in C_CONDITIONS:
                        for pl in PLANNING_MODES:
                            c[("C", arm, lat, acq, cond, pl)] = N_C_PAIRS
    elif experiment == "D":
        for arm in ARMS_D:
            for lv in LOAD_LEVELS:
                for d in DELTAS_SHORT:
                    c[("D", arm, lv, "async_scope", d)] = N_D
    elif experiment == "E":
        for arm in ARMS_E:
            for cond in ("async_scope", "async_evidence_last_literal"):
                for d in DELTAS_SHORT:
                    c[("E", arm, cond, d)] = N_E
    elif experiment == "F":
        for arm, impl in ARMS_F:
            for d in DELTAS_SHORT:
                c[("F", arm, impl, "async_scope", d)] = N_F
    elif experiment == "G":
        for arm in ARMS_G:
            for cond in G_CONDITIONS:
                c[("G", arm, cond)] = N_G
    return c


def cell_key(t):
    """Canonical cell key of a normalised trial / oracle row (dict with canonical fields)."""
    x = t.get("experiment")
    d = norm_delta(t.get("delta_ms"))
    if x == "A":
        return ("A", t.get("arm_c"), t.get("client_impl"), t.get("condition_c"), d)
    if x == "B":
        return ("B", t.get("arm_c"), t.get("condition_c"), norm_float(t.get("lambda_per_s")))
    if x == "C":
        return ("C", t.get("arm_c"), norm_float(t.get("injected_latency_ms")), t.get("acquisition_mode"),
                t.get("condition_c"), t.get("planning_mode"))
    if x == "D":
        return ("D", t.get("arm_c"), t.get("load_level"), t.get("condition_c"), d)
    if x == "E":
        return ("E", t.get("arm_c"), t.get("condition_c"), d)
    if x == "F":
        return ("F", t.get("arm_c"), t.get("client_impl"), t.get("condition_c"), d)
    if x == "G":
        return ("G", t.get("arm_c"), t.get("condition_c"))
    return (str(x),)


def _sk(v):
    if v is None:
        return (0, "")
    if isinstance(v, (int, float)):
        return (1, float(v), "")
    for seq in (ARMS, CLIENT_IMPLS, CONDITIONS, LOAD_LEVELS, ACQ_MODES, PLANNING_MODES, EXPERIMENTS):
        if v in seq:
            return (1, float(seq.index(v)), str(v))
    return (2, 0.0, str(v))


def cell_sort_key(cell):
    return tuple(_sk(v) for v in cell)


def cell_label(cell):
    return "/".join("NA" if v is None else ("%g" % v if isinstance(v, float) else str(v)) for v in cell)


def norm_delta(d):
    if d is None or d == "":
        return None
    return float(d)


norm_float = norm_delta


def deltas_equal(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) < 1e-9


def delta_label(d):
    return "NA" if d is None else ("%g" % float(d))


# ----------------------------------------------------------------------------- policy
POLICY_OBJECTS = ("evidence-a", "evidence-b", "evidence-c", "evidence-d", "evidence-e",
                  "evidence-f", "response-scope", "application-policy")
_ALIASES = {
    "a": "evidence-a", "b": "evidence-b", "c": "evidence-c", "d": "evidence-d",
    "e": "evidence-e", "f": "evidence-f",
    "scope": "response-scope", "scope_ok": "response-scope", "response_scope": "response-scope",
    "e38.scope": "response-scope", "e39.scope": "response-scope",
    "app_policy": "application-policy", "application_policy": "application-policy",
    "app-policy": "application-policy", "app_policy_allowed": "application-policy",
    "e38.app-policy": "application-policy", "e39.app-policy": "application-policy",
    "epoch": "e38-epoch", "e38_epoch": "e38-epoch",
}
for _x in "abcdef":
    _ALIASES["evidence_" + _x] = "evidence-" + _x
    _ALIASES["e38.evidence-" + _x] = "evidence-" + _x
    _ALIASES["e38.evidence." + _x] = "evidence-" + _x


def norm_object(name):
    """Canonical policy-object name. Accepts 'a', 'evidence-a', 'configmaps/evidence-a',
    'ns/evidence-a', and the A4 Pod annotation keys 'e38.evidence/a', 'e38.scope', 'e38.app-policy'."""
    if name is None:
        return None
    s = str(name).strip()
    sl = s.lower()
    if sl in _ALIASES:
        return _ALIASES[sl]
    if "/" in s:
        s = s.rsplit("/", 1)[1]
    s_l = s.lower()
    return _ALIASES.get(s_l, s_l)


TRUE_STRINGS = {"true", "allowed", "allow", "yes", "1"}
FALSE_STRINGS = {"false", "not allowed", "not_allowed", "not-allowed", "notallowed",
                 "denied", "deny", "disallowed", "no", "0"}


def parse_bool(v):
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
    """J = (a AND b) OR (c AND d) OR (e AND f); V_core = J AND scope_ok AND app_policy_allowed.
    Returns (value or None, missing list)."""
    missing = [o for o in POLICY_OBJECTS if state.get(o) is None]
    if missing:
        return None, missing
    g = lambda x: state["evidence-" + x]
    j = (g("a") and g("b")) or (g("c") and g("d")) or (g("e") and g("f"))
    return bool(j and state["response-scope"] and state["application-policy"]), []


# ----------------------------------------------------------------------------- statistics
def wilson(k, n, z=Z95):
    """Wilson score interval -> (p_hat, lo, hi); (None, None, None) when n == 0."""
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


def newcombe_diff(k1, n1, k2, n2, z=Z95):
    """Newcombe (1998) method 10 (hybrid score) CI for p1 - p2 from two Wilson intervals.
    Returns (diff, lo, hi) or (None, None, None)."""
    p1, l1, u1 = wilson(k1, n1, z)
    p2, l2, u2 = wilson(k2, n2, z)
    if p1 is None or p2 is None:
        return None, None, None
    d = p1 - p2
    lo = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return d, max(-1.0, lo), min(1.0, hi)


def _binom_pmf_vec(n, p):
    import numpy as np
    from scipy.stats import binom
    return binom.pmf(np.arange(n + 1), n, p)


def exact_unconditional_diff(k1, n1, k2, n2, alpha=0.05, grid=1001):
    """Exact unconditional CI for p1 - p2 (Santner-Snell tail method: ordering by the observed
    difference, nuisance parameter maximised over a grid). Conservative (coverage >= 1 - alpha up to
    the grid resolution). Sensitivity companion to newcombe_diff. Returns (diff, lo, hi)."""
    import numpy as np
    from scipy.stats import binom
    if not n1 or not n2:
        return None, None, None
    d_obs = k1 / n1 - k2 / n2
    x1 = np.arange(n1 + 1)[:, None] / n1
    x2 = np.arange(n2 + 1)[None, :] / n2
    D = x1 - x2
    ge = D >= d_obs - 1e-12
    le = D <= d_obs + 1e-12
    k1s, k2s = np.arange(n1 + 1), np.arange(n2 + 1)

    def pmax(delta, mask):
        lo_p2 = max(0.0, -delta)
        hi_p2 = min(1.0, 1.0 - delta)
        if hi_p2 < lo_p2:
            return 0.0
        p2 = np.linspace(lo_p2, hi_p2, grid)
        p1 = np.clip(p2 + delta, 0.0, 1.0)
        a = binom.pmf(k1s[:, None], n1, p1[None, :])          # (n1+1, G)
        b = binom.pmf(k2s[:, None], n2, p2[None, :])          # (n2+1, G)
        v = np.einsum("ig,ij,jg->g", a, mask.astype(float), b)
        return float(v.max())

    def bound(upper):
        # upper bound: largest delta with P(D <= d_obs | delta) > alpha/2; lower: smallest with P(D >= d_obs) > alpha/2
        mask = le if upper else ge
        a, b = (d_obs, 1.0) if upper else (-1.0, d_obs)
        for _ in range(40):
            mid = (a + b) / 2
            ok = pmax(mid, mask) > alpha / 2
            if upper:
                a, b = (mid, b) if ok else (a, mid)
            else:
                a, b = (a, mid) if ok else (mid, b)
        return a if upper else b

    return d_obs, max(-1.0, bound(False)), min(1.0, bound(True))


def topup_decision(p_hat):
    if p_hat is None:
        return False
    return TOPUP_LO < p_hat < TOPUP_HI


def exposure_d_star(deltas, phats, thr=EXPOSURE_THRESHOLD):
    """Smallest grid delta d* with p_hat <= thr at d* and at every larger grid delta (None p_hat blocks)."""
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


def window_area_ms(deltas, phats):
    """Trapezoidal area under p_hat(delta) over the grid (ms): the E39-A estimate of the effective
    exposure window W used to predict E39-B (p = 0 assumed beyond the largest grid delta).
    None p_hat -> None (cannot integrate)."""
    if not deltas or any(p is None for p in phats):
        return None
    a = 0.0
    for i in range(1, len(deltas)):
        a += (deltas[i] - deltas[i - 1]) * (phats[i] + phats[i - 1]) / 2.0
    return a


def hodges_lehmann_two_sample(x, y):
    import numpy as np
    x, y = np.asarray(x, float), np.asarray(y, float)
    return float(np.median(np.subtract.outer(x, y)))


def hodges_lehmann_paired(d):
    """One-sample HL estimator of paired differences: median of Walsh averages (i <= j)."""
    import numpy as np
    d = np.asarray(d, float)
    if not len(d):
        return None
    i, j = np.triu_indices(len(d))
    return float(np.median((d[i] + d[j]) / 2.0))


def order_stat_ci(vals, alpha=0.05):
    import numpy as np
    v = np.sort(np.asarray(vals, dtype=float))
    b = len(v)
    if not b:
        return None, None
    lo = v[max(0, int(math.floor(alpha / 2 * b)))]
    hi = v[min(b - 1, int(math.ceil((1 - alpha / 2) * b)) - 1)]
    f = lambda x: None if (math.isinf(x) or math.isnan(x)) else float(x)
    return f(lo), f(hi)


def poisson_window_mle(ks, ns, lams):
    """MLE of W (seconds) in p_i = 1 - exp(-lam_i * W) from binomial counts (k_i of n_i) at rates lam_i
    (per second), with a profile-likelihood 95% interval (chi2_1 = 3.841). Returns dict."""
    import numpy as np
    from scipy.optimize import minimize_scalar
    ks, ns, lams = (np.asarray(v, float) for v in (ks, ns, lams))
    if not len(ks) or ns.sum() <= 0:
        return {"W_s": None, "lo_s": None, "hi_s": None, "boundary": "no_data"}

    def nll(W):
        W = max(W, 1e-15)
        p = -np.expm1(-lams * W)
        p = np.clip(p, 1e-300, 1.0)
        return -float(np.sum(ks * np.log(p) + (ns - ks) * (-lams * W)))

    if ks.sum() == 0:
        # MLE at the boundary W = 0; upper profile bound only
        W_hat, boundary = 0.0, "zero_events"
        f0 = 0.0
    else:
        hi_guess = 50.0 / max(lams.min(), 1e-12)
        if np.all(ks == ns):
            W_hat, boundary = math.inf, "all_events"
        else:
            r = minimize_scalar(nll, bounds=(1e-12, hi_guess), method="bounded",
                                options={"xatol": 1e-12, "maxiter": 500})
            W_hat, boundary = float(r.x), ""
        f0 = nll(W_hat) if math.isfinite(W_hat) else nll(hi_guess)
    crit = 3.841458820694124 / 2.0

    def root(a, b):
        fa = nll(a) - f0 - crit
        fb = nll(b) - f0 - crit
        if fa * fb > 0:
            return None
        for _ in range(200):
            m = (a + b) / 2
            fm = nll(m) - f0 - crit
            if fa * fm <= 0:
                b, fb = m, fm
            else:
                a, fa = m, fm
        return (a + b) / 2

    top = 100.0 / max(lams.min(), 1e-12)
    if not math.isfinite(W_hat):
        return {"W_s": None, "lo_s": None, "hi_s": None, "boundary": boundary}
    lo = 0.0 if W_hat <= 1e-12 or boundary == "zero_events" else root(1e-15, W_hat)
    hi = root(max(W_hat, 1e-15), top)
    return {"W_s": W_hat, "lo_s": lo, "hi_s": hi, "boundary": boundary}


# ----------------------------------------------------------------------------- IO helpers
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_jsonl(path):
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
    from datetime import timedelta
    secs, rem = divmod(int(ns), 1_000_000_000)
    dt = datetime(1970, 1, 1) + timedelta(seconds=secs)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + ".%06dZ" % (rem // 1000)


def rv_int(rv):
    if rv is None or isinstance(rv, bool):
        return None
    s = str(rv).strip()
    if not s.isdigit():
        return None
    return int(s)


_PRECOND_RV = re.compile(r"ResourceVersion in precondition:\s*(\S+?),\s*ResourceVersion in object meta:\s*(\S+?)(?:[\s\"'.,)]|$)")
_PRECOND_UID = re.compile(r"UID in precondition:\s*(\S+?),\s*UID in object meta:\s*(\S*?)(?:[\s\"'.,)]|$)")
_PRECOND_RV_V137 = re.compile(r"the ResourceVersion in the precondition \(([^()\s]+)\) does not match the "
                              r"ResourceVersion in record \(([^()\s]*)\)")
_PRECOND_UID_V137 = re.compile(r"the UID in the precondition \(([^()\s]+)\) does not match the UID in record "
                               r"\(([^()\s]*)\)")


def parse_precondition_message(msg):
    """kube-apiserver storage precondition failure (409 Conflict) message ->
    {"kind": "resourceVersion"|"uid"|None, "precondition": str, "current": str}.
    Format (apiserver storage.Preconditions.Check): 'Precondition failed: ResourceVersion in
    precondition: <p>, ResourceVersion in object meta: <c>' / '... UID in precondition: ...'.
    kube-apiserver v1.37 (observed in the e39 cluster probe, 2026-09-26, before the LOCK) formats the same
    check as 'Operation cannot be fulfilled on Pod "<n>": the ResourceVersion in the precondition (<p>) does
    not match the ResourceVersion in record (<c>). ...' / '... the UID in the precondition (<p>) does not match
    the UID in record (<c>). ...'; both formats are parsed, "current" = the record (object meta) value."""
    if not msg:
        return {"kind": None, "precondition": None, "current": None}
    s = str(msg)
    for rx, kind in ((_PRECOND_RV, "resourceVersion"), (_PRECOND_RV_V137, "resourceVersion"),
                     (_PRECOND_UID, "uid"), (_PRECOND_UID_V137, "uid")):
        m = rx.search(s)
        if m:
            return {"kind": kind, "precondition": m.group(1), "current": m.group(2)}
    return {"kind": None, "precondition": None, "current": None}
