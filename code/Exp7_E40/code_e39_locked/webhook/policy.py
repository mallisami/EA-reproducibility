"""E38v2 validity policy -- pure function, no I/O.

Interface spec E38v2 v1 (2026-09-26), VALIDITY POLICY:
    J      = (a AND b) OR (c AND d) OR (e AND f)
    V_core = J AND scope_allowed AND app_policy_allowed

Inputs are the RAW string values read from the eight ConfigMaps in the test
namespace:
    evidence-a .. evidence-f   data.value    "true" / "false"
    response-scope             data.allowed  "true" / "false"
    application-policy         data.allowed  "true" / "false"

Fail closed: a missing ConfigMap, a missing key, or any value that is not
exactly the string "true" or "false" makes the whole evaluation DENY, even if
the value would not have been needed to decide J.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

EVIDENCE = ("a", "b", "c", "d", "e", "f")
EVIDENCE_CMS = tuple("evidence-" + k for k in EVIDENCE)
SCOPE_CM = "response-scope"
APP_POLICY_CM = "application-policy"
# Order in which the webhook reads the objects (and logs them).
CONFIGMAPS = EVIDENCE_CMS + (SCOPE_CM, APP_POLICY_CM)
# data key that carries the value in each ConfigMap.
VALUE_KEY = dict({cm: "value" for cm in EVIDENCE_CMS}, **{SCOPE_CM: "allowed", APP_POLICY_CM: "allowed"})
DISJUNCTS = (("a", "b"), ("c", "d"), ("e", "f"))


def extract_value(name: str, configmap: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Return data[<key>] of a ConfigMap object as given by the API, or None."""
    if not isinstance(configmap, Mapping):
        return None
    data = configmap.get("data")
    if not isinstance(data, Mapping):
        return None
    value = data.get(VALUE_KEY[name])
    return value if isinstance(value, str) else None


def parse_bool(value: Any) -> Optional[bool]:
    """Strict: only the exact strings "true"/"false" parse; everything else -> None."""
    if value == "true" and isinstance(value, str):
        return True
    if value == "false" and isinstance(value, str):
        return False
    return None


def evaluate(values: Mapping[str, Any]) -> Tuple[bool, str]:
    """Return (allow, reason). values maps ConfigMap name -> raw string value."""
    if not isinstance(values, Mapping):
        return False, "deny: values not a mapping"
    parsed = {}
    bad = []
    for name in CONFIGMAPS:
        raw = values.get(name)
        b = parse_bool(raw)
        if b is None:
            bad.append("%s=%s" % (name, "<missing>" if raw is None else repr(raw)[:40]))
        parsed[name] = b
    if bad:
        return False, "deny: missing_or_unparseable " + ",".join(bad)

    ev = {k: parsed["evidence-" + k] for k in EVIDENCE}
    satisfied = [x + "&" + y for (x, y) in DISJUNCTS if ev[x] and ev[y]]
    j = bool(satisfied)
    scope_ok = parsed[SCOPE_CM]
    app_ok = parsed[APP_POLICY_CM]

    if j and scope_ok and app_ok:
        return True, "allow: J via %s; scope_allowed; app_policy_allowed" % "|".join(satisfied)
    failures = []
    if not j:
        failures.append("J_false")
    if not scope_ok:
        failures.append("scope_not_allowed")
    if not app_ok:
        failures.append("app_policy_not_allowed")
    return False, "deny: " + ";".join(failures)
