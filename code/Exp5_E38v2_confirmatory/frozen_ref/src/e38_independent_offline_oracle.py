#!/usr/bin/env python3
"""Offline E38 protocol semantics and exhaustive certificate checks, not an E38 run.

Independent of the DCJI implementation. This verifies proposed truth-table
semantics and trial-accounting invariants before independent non-author review.
It does NOT prove a Kubernetes protocol, cross-resource coherence, or actuation.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from itertools import combinations, product
from typing import Iterable

EVIDENCE = ('a','b','c','d','e','f')
OBSERVATIONS = EVIDENCE + ('uid','scope')
DECISION_UID = 'uid-original'
REPLACEMENT_UID = 'uid-replacement'

@dataclass(frozen=True)
class State:
    a: bool = True
    b: bool = True
    c: bool = True
    d: bool = True
    e: bool = True
    f: bool = True
    uid: str = DECISION_UID
    scope: bool = True

    @property
    def justification(self) -> bool:
        return (self.a and self.b) or (self.c and self.d) or (self.e and self.f)

    @property
    def valid(self) -> bool:
        return self.justification and self.uid == DECISION_UID and self.scope


def truth_from_state(state: State) -> str:
    """Independent semantic oracle: action is valid iff J and bind and scope."""
    return 'EXECUTE' if state.valid else 'ABORT_REPLAN'


def hybrid(decision: State, current: State, acquired: Iterable[str]) -> State:
    picked = frozenset(acquired)
    if picked - frozenset(OBSERVATIONS):
        raise ValueError('Unknown observation ID: ' + str(sorted(picked - frozenset(OBSERVATIONS))))
    return State(**{k: getattr(current if k in picked else decision, k) for k in OBSERVATIONS})


def certificate_rejects_all(decision: State, invalid: list[State], acquired: Iterable[str]) -> bool:
    return all(truth_from_state(hybrid(decision, now, acquired)) == 'ABORT_REPLAN' for now in invalid)


def enumerate_exact_minimum(decision: State, invalid: list[State]) -> tuple[tuple[str,...],...]:
    """Exhaustive truth-table oracle, no import/call to the planner under review."""
    for n in range(len(OBSERVATIONS)+1):
        hits = tuple(s for s in combinations(OBSERVATIONS,n) if certificate_rejects_all(decision,invalid,s))
        if hits: return hits
    raise AssertionError('No certificate; full refresh should reject every valid oracle-labeled invalid state')


def asymmetric_goals() -> list[State]:
    s = State()
    return [replace(s,a=False,c=False,e=False), replace(s,uid=REPLACEMENT_UID), replace(s,scope=False)]


def symmetric_goals() -> list[State]:
    s = State()
    witnesses=[]
    for doomed in product(('a','b'),('c','d'),('e','f')):
        witnesses.append(replace(s,**{literal:False for literal in doomed}))
    return witnesses + [replace(s,uid=REPLACEMENT_UID), replace(s,scope=False)]


def validate_trial_accounting(row:dict) -> tuple[bool,str]:
    """Protocol-contract aligned conservative QC; NEVER drop flagged experimental trials.

    This does not independently validate measured truth or HTTP payloads. All flagged
    trials must remain in the denominator and be reported separately for adjudication.
    """
    import json
    from pathlib import Path
    contract = json.loads((Path(__file__).resolve().parent/'E38_TRIAL_CONTRACT.json').read_text())
    required = contract['required_fields']
    nullable = {'post_effect_uid', 'first_effect_elapsed_ms', 'server_http_status', 'log_reference'}
    missing = [key for key in required if key not in row or (row[key] is None and key not in nullable)]
    if missing: return False,'Required contract fields missing or null: '+','.join(missing)
    if row['guard_decision'] not in ('EXECUTE','ABORT_REPLAN') or row['oracle_decision'] not in ('EXECUTE','ABORT_REPLAN'):
        return False,'Invalid gate/oracle decision'
    for key in ('delete_sent','uid_precondition_sent','pod_absent','coherent','gate_deadline_missed',
                'effect_deadline_missed','failed','aborted','timeout'):
        if type(row[key]) is not bool: return False,'Not a boolean: '+key
    if type(row['get_count']) is not int or row['get_count'] < 0:
        return False,'Invalid physical GET count'
    if row['guard_decision']=='ABORT_REPLAN' and row['delete_sent']:
        return False,'Response delete dispatched after client abort'
    if row['delete_sent'] and not row['uid_precondition_sent']:
        return False,'Missing server-side UID precondition'
    if row['delete_sent'] and row['method']!='NativePrecondition' and row['decision_uid'] != row['observed_uid']:
        return False,'Mismatched pre-dispatch UID: preserve and independently adjudicate'
    if row['server_http_status']==409 and row['pod_absent']:
        return False,'HTTP 409 conflict cannot establish a completed delete effect'
    if row['pod_absent'] and (not row['delete_sent'] or row['server_http_status'] not in (200,202)):
        return False,'An observed deletion effect requires a sent and accepted DELETE'
    if not row['coherent'] and row['guard_decision']=='EXECUTE':
        return False,'Execution with no verified coherent view'
    if row['guard_decision']!=row['oracle_decision']:
        return False,'Oracle decision mismatch: preserve as a failure; never exclude trial'
    return True,'Contract and preliminary QC passed; external state/history/HTTP payload still require audit'
