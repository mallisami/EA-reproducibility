# FlashIR E9 — Formal Negative Controls

E9 instantiates all five frozen negative-control mechanisms.

| Control | Oracle valid at commit | Oracle EA-TOCTOU attack goal | Result |
|---|---:|---:|---|
| historical fact persists | True | False | PASS |
| non-attacker transition | True | False | PASS |
| immutable target | True | False | PASS |
| one basis breaks while another remains | True | False | PASS |
| F5.6 uncuttable historical basis | True | False | PASS |

## Controls

1. **Historical fact persists** — attacker removes live activity, but the policy is
   intentionally based on a historical fact that remains physically true.
2. **Non-attacker transition** — an environment-only irrelevant change occurs after decision.
3. **Immutable target** — an attacker changes unrelated mutable state while the queued target
   remains bound to an immutable UID.
4. **One basis breaks while another remains** — one sufficient justification basis is cut,
   but another independent sufficient basis remains true.
5. **F5.6 uncuttable historical basis** — the attacker removes the current-threat basis, but
   the frozen historical basis remains sufficient.

Across all five controls:
- oracle `valid_at_commit = true`;
- oracle `attack_goal = false`;
- authorization remains valid;
- no applicable defense produces an unsafe commit;
- no applicable defense produces a false abort.

## Interpretation

FlashIR does **not** define EA-TOCTOU as merely "some state changed after the decision."
A positive requires the pending action to become unjustified, misbound, or unsafe in scope
at commit while the attacker-realizable transition semantics and action consequence conditions hold.

The negative controls explicitly reject:
- irrelevant environmental drift,
- immutable historical facts becoming falsely "invalid",
- immutable-target rebinding,
- partial basis loss when another sufficient basis survives,
- adversarial removal of a live basis when an uncuttable historical basis still justifies the action.
