"""Statistics / rule tests: Wilson CI, Hodges-Lehmann, exposure window d*, stage-2 top-up rule."""
import math

import numpy as np
import pytest

from analyze_e38v2 import _order_stat_ci, hodges_lehmann
from e38v2_common import DELTAS_MS, exposure_d_star, topup_decision, wilson
from stage2_topup import topup_from_cells


def wilson_ref(k, n, z=1.959963984540054):
    # independent textbook form: (p + z^2/2n +- z sqrt(p(1-p)/n + z^2/4n^2)) / (1 + z^2/n)
    p = k / n
    a = p + z * z / (2 * n)
    b = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    c = 1 + z * z / n
    return (a - b) / c, (a + b) / c


@pytest.mark.parametrize("k,n,lo,hi", [
    (0, 39, 0.0, 0.089667),
    (0, 93, 0.0, 0.039668),
    (39, 39, 0.910333, 1.0),
    (5, 10, 0.236593, 0.763407),
])
def test_wilson_known_values(k, n, lo, hi):
    p, l, h = wilson(k, n)
    assert p == pytest.approx(k / n)
    assert l == pytest.approx(lo, abs=2e-6)
    assert h == pytest.approx(hi, abs=2e-6)


def test_wilson_matches_reference_and_symmetry():
    for n in (1, 10, 39, 93):
        for k in range(n + 1):
            _, l, h = wilson(k, n)
            rl, rh = wilson_ref(k, n)
            assert l == pytest.approx(max(0.0, rl), abs=1e-12) or k == 0
            assert h == pytest.approx(min(1.0, rh), abs=1e-12) or k == n
            _, l2, h2 = wilson(n - k, n)
            assert l == pytest.approx(1 - h2, abs=1e-12)
            assert l <= k / n <= h


def test_wilson_equals_score_test_inversion():
    # Wilson bounds are the roots in p of (p_hat - p)^2 = z^2 p (1-p) / n  (independent method)
    z = 1.959963984540054
    for k, n in [(1, 39), (2, 39), (7, 39), (20, 93), (88, 93)]:
        ph = k / n
        roots = sorted(np.roots([1 + z * z / n, -(2 * ph + z * z / n), ph * ph]).real)
        _, l, h = wilson(k, n)
        assert l == pytest.approx(roots[0], abs=1e-10) and h == pytest.approx(roots[1], abs=1e-10)


def test_wilson_empty():
    assert wilson(0, 0) == (None, None, None)
    with pytest.raises(ValueError):
        wilson(3, 2)


def test_hodges_lehmann():
    assert hodges_lehmann([1, 2, 3], [0]) == 2
    assert hodges_lehmann([10, 11], [1, 2]) == 9
    assert hodges_lehmann([5, 5, 5], [5, 5]) == 0


def test_order_stat_ci_with_inf():
    lo, hi = _order_stat_ci([1.0] * 97 + [math.inf] * 3)
    assert lo == 1.0 and hi is None
    lo, hi = _order_stat_ci(list(range(1000)))
    assert lo == 25 and hi == 974


def test_exposure_d_star():
    D = list(DELTAS_MS)
    z = [0.0] * len(D)
    assert exposure_d_star(D, z) == (-5.0, None)
    p = list(z); p[D.index(1.0)] = 0.2
    assert exposure_d_star(D, p) == (2.0, 1.0)
    p = list(z); p[-1] = 0.1
    assert exposure_d_star(D, p) == (None, 50.0)
    p = list(z); p[D.index(10.0)] = None
    assert exposure_d_star(D, p)[0] == 20.0
    p = [0.9] * 4 + [0.05] * 8          # exactly 0.05 counts as closed (<=)
    assert exposure_d_star(D, p) == (0.5, 0.0)
    # non-monotone: a late spike reopens the window
    p = [0.0] * 3 + [0.5] + [0.0] * 5 + [0.2] + [0.0] * 2
    assert exposure_d_star(D, p) == (20.0, 10.0)


@pytest.mark.parametrize("p,want", [(0.0, False), (0.05, False), (2 / 39, True), (0.5, True),
                                    (37 / 39, True), (0.95, False), (1.0, False), (None, False)])
def test_topup_decision(p, want):
    assert topup_decision(p) is want


def test_topup_list_deterministic_and_filtered():
    cells = [
        {"arm": "A3_dcji_webhook", "condition": "async_scope", "delta_ms": 2.0, "n_planned": 39, "p_hat": 0.3},
        {"arm": "A1_dcji", "condition": "async_scope", "delta_ms": 0.5, "n_planned": 39, "p_hat": 0.5},
        {"arm": "A1_dcji", "condition": "async_scope", "delta_ms": -1.0, "n_planned": 39, "p_hat": 0.1},
        {"arm": "A1_dcji", "condition": "no_write", "delta_ms": None, "n_planned": 39, "p_hat": 0.5},
        {"arm": "A1_dcji", "condition": "uid_rebind_control", "delta_ms": None, "n_planned": 10, "p_hat": 0.5},
        {"arm": "A0_native_uid", "condition": "async_app_policy", "delta_ms": 3.0, "n_planned": 39, "p_hat": 1.0},
        {"arm": "A0_native_uid", "condition": "async_evidence_last_literal", "delta_ms": 3.0, "n_planned": 39, "p_hat": 0.94},
    ]
    out = topup_from_cells(cells)
    keys = [(r["arm"], r["condition"], r["delta_ms"]) for r in out]
    assert keys == [("A0_native_uid", "async_evidence_last_literal", 3.0),
                    ("A1_dcji", "async_scope", -1.0), ("A1_dcji", "async_scope", 0.5),
                    ("A3_dcji_webhook", "async_scope", 2.0)]
    assert all(r["n_topup"] == 54 for r in out)
    assert [r["order"] for r in out] == [0, 1, 2, 3]
    assert topup_from_cells(list(reversed(cells))) == out
