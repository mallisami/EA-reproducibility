import sys
from pathlib import Path

import pytest

A = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(A))
sys.path.insert(0, str(A / "fixtures"))

import e39_testkit as kit  # noqa: E402


@pytest.fixture(scope="session")
def mini_run(tmp_path_factory):
    """All seven sub-experiments, mini design (n=3 per cell; B n=20; C 3 pairs per cell)."""
    return kit.make_run(tmp_path_factory.mktemp("mini"), ["A", "B", "C", "D", "E", "F", "G"], mini=True)


@pytest.fixture(scope="session")
def mini_results(mini_run, tmp_path_factory):
    import json
    import analyze_e39 as an
    out = tmp_path_factory.mktemp("mini_out")
    rc = an.main(kit.analyze_args(mini_run, out, "--w-external", "A2=2.5"))
    assert rc == 0
    return json.loads((out / "results_E39.json").read_text()), out
