# F5.LIVE interpretation note (V11.1)

This directory preserves the original F5.LIVE scenario, execution script, and
result JSON unchanged for provenance.

F5.LIVE establishes two things:

1. the unchanged DCJI planner selects three of six **logical** multi-basis
   predicates (`flag_a`, `flag_c`, `flag_e`); and
2. across 20 live repetitions, the DCJI-style and FullRefresh-style gates both
   return the correct `ABORT_REPLAN` decision with zero discordance.

It does **not** establish three physical Kubernetes reads versus six. In
`run_live_multi.py`, `live_query()` first retrieves the complete Pod JSON object
with one `kubectl get pod ... -o json` call and then projects the selected label
values locally. Thus the recorded `B6_query_cost=3` and `B4_query_cost=6` are
normalized logical-predicate counts, not measured API-call or latency costs.

The independent physical-I/O benchmark is E21 under
`../e21_physical_cost/`: six predicates are hosted on six separate ConfigMaps,
physical HTTP GETs are logged per resource, and refresh latency/payload are
measured directly.

See also `../../06_revision_audit/live_query_semantics_audit.json`.
