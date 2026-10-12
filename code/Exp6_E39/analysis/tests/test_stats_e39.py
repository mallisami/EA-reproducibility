import math
import random

import pytest

from e39_common import (canon_arm, canon_condition, exact_unconditional_diff, exposure_d_star,
                        hodges_lehmann_paired, hodges_lehmann_two_sample, newcombe_diff, norm_object,
                        parse_precondition_message, poisson_window_mle, topup_decision, wilson, window_area_ms,
                        design_cells)


def test_wilson_known_values():
    p, lo, hi = wilson(5, 39)
    assert abs(p - 5 / 39) < 1e-12
    assert abs(lo - 0.0560) < 5e-4 and abs(hi - 0.2667) < 5e-4
    assert wilson(0, 39)[1] == 0.0 and wilson(39, 39)[2] == 1.0
    assert wilson(0, 0) == (None, None, None)


def test_newcombe_reference_example():
    # Newcombe (1998) Stat Med 17:873, example 56/70 vs 48/80, method 10: 0.0524 to 0.3339
    d, lo, hi = newcombe_diff(56, 70, 48, 80)
    assert abs(d - (0.8 - 0.6)) < 1e-12
    assert abs(lo - 0.0524) < 1e-3 and abs(hi - 0.3339) < 1e-3


def test_newcombe_extremes_and_symmetry():
    d, lo, hi = newcombe_diff(39, 39, 0, 39)
    assert d == 1.0 and lo > 0.85 and hi == 1.0
    d2, lo2, hi2 = newcombe_diff(0, 39, 39, 39)
    assert abs(lo2 + hi) < 1e-12 and abs(hi2 + lo) < 1e-12
    assert newcombe_diff(1, 0, 1, 5) == (None, None, None)


def test_exact_unconditional_contains_estimate_and_is_wider_than_newcombe_at_extremes():
    d, lo, hi = exact_unconditional_diff(39, 39, 0, 39, grid=201)
    assert d == 1.0 and 0.8 < lo < 1.0 and hi == pytest.approx(1.0, abs=1e-6)
    d, lo, hi = exact_unconditional_diff(20, 39, 10, 39, grid=201)
    assert lo < d < hi
    _, nlo, nhi = newcombe_diff(20, 39, 10, 39)
    assert lo <= nlo + 0.02 and hi >= nhi - 0.02


def test_hodges_lehmann_paired_and_two_sample():
    assert hodges_lehmann_paired([1.0, 2.0, 3.0]) == pytest.approx(2.0)
    # Walsh averages of (0,0,10): 0,0,10,0,5,5 -> median 2.5
    assert hodges_lehmann_paired([0.0, 0.0, 10.0]) == pytest.approx(2.5)
    assert hodges_lehmann_paired([-1.0, 1.0]) == pytest.approx(0.0)
    assert hodges_lehmann_two_sample([3, 4, 5], [1, 2, 3]) == pytest.approx(2.0)
    assert hodges_lehmann_paired([]) is None


def test_poisson_window_mle_recovers_known_window():
    rnd = random.Random(7)
    W = 0.004   # 4 ms
    lams, ks, ns = [1.0, 10.0, 100.0], [], []
    for lam in lams:
        p = 1 - math.exp(-lam * W)
        ns.append(4000)
        ks.append(sum(1 for _ in range(4000) if rnd.random() < p))
    r = poisson_window_mle(ks, ns, lams)
    assert r["W_s"] == pytest.approx(W, rel=0.15)
    assert r["lo_s"] < r["W_s"] < r["hi_s"]


def test_poisson_window_mle_zero_events_gives_upper_bound_only():
    r = poisson_window_mle([0, 0, 0], [400, 400, 400], [1.0, 10.0, 100.0])
    assert r["W_s"] == 0.0 and r["lo_s"] == 0.0 and r["boundary"] == "zero_events"
    # rule of three-like: 400 trials at 100/s -> upper W about 3/(400*111) s
    assert 0 < r["hi_s"] < 0.001


def test_window_area_trapezoid():
    assert window_area_ms([0.0, 2.0, 5.0], [1.0, 0.5, 0.0]) == pytest.approx(1.5 + 0.75)
    assert window_area_ms([0.0, 2.0], [1.0, None]) is None


def test_exposure_d_star():
    d = [0.0, 1.0, 2.0, 5.0]
    assert exposure_d_star(d, [0.9, 0.3, 0.0, 0.0]) == (2.0, 1.0)
    assert exposure_d_star(d, [0.9, 0.3, 0.2, 0.1]) == (None, 5.0)
    assert exposure_d_star(d, [0.0, None, 0.0, 0.0]) == (2.0, None)


def test_precondition_message_parsing():
    m = ('Operation cannot be fulfilled on pods "t": Precondition failed: ResourceVersion in precondition: 123, '
         'ResourceVersion in object meta: 130')
    assert parse_precondition_message(m) == {"kind": "resourceVersion", "precondition": "123", "current": "130"}
    m2 = 'Precondition failed: UID in precondition: aaa-1, UID in object meta: bbb-2'
    assert parse_precondition_message(m2)["kind"] == "uid"
    assert parse_precondition_message("conflict")["kind"] is None
    assert parse_precondition_message(None)["kind"] is None


def test_canonical_names_from_record_schema():
    assert canon_arm("A1_dcji") == ("A1", "python")
    assert canon_arm("A1_dcji_go") == ("A1", "client-go")
    assert canon_arm("A4d_commit_direct_go", "client-go") == ("A4d", "client-go")
    assert canon_arm("A4m_commit_mirror") == ("A4m", "python")
    assert canon_arm("bogus")[0] is None
    assert canon_condition("poisson_scope") == "async_scope"
    assert canon_condition("out_of_model_bdf") == "out_of_model_cut_bdf"
    assert canon_condition("in_model_ace") == "in_model_cut_ace"
    assert canon_condition("nonsense") is None


def test_pod_annotation_keys_normalise():
    assert norm_object("e38.evidence/a") == "evidence-a"
    assert norm_object("e38.scope") == "response-scope"
    assert norm_object("e38.app-policy") == "application-policy"
    assert norm_object("configmaps/evidence-f") == "evidence-f"


def test_topup_rule_strict():
    assert not topup_decision(0.05) and not topup_decision(0.95) and not topup_decision(None)
    assert topup_decision(0.06) and topup_decision(0.94)


def test_design_cell_totals_match_spec():
    tot = {x: sum(design_cells(x).values()) for x in "ABCDEFG"}
    assert tot == {"A": 5344, "B": 4800, "C": 1920 * 2, "D": 1872, "E": 1248, "F": 936, "G": 156}
