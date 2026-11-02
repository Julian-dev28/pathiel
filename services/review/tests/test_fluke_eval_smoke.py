"""Keep the periodic eval (services/review/evals/fluke_eval.py) from rotting:
a few seeded runs of each scenario through the real labeller. The pass
rates themselves are the eval's job (200 runs); this only checks the
extremes, which hold in every run."""
from __future__ import annotations

from services.review import stats as S
from services.review.evals import fluke_eval as E


def test_scenarios_run_and_report():
    r = E.run_scenario("small_n", runs=3)
    assert r["rate"] == 0.0 and r["pass"] and r["runs"] == 3


def test_a_huge_planted_edge_is_found_and_flat_noise_is_not():
    big = E.label_rate(5, 40, 120, E.normal(1.5, 0.5), E.normal(0.0, 0.5), n_boot=300, n_perm=300)
    assert big[S.EDGE] == 1.0
    flat = E.label_rate(5, 40, 120, E.normal(0.0, 0.5), E.normal(0.0, 0.5), n_boot=300, n_perm=300)
    assert flat[S.EDGE] + flat[S.LEAK] == 0.0


def test_streams_are_seeded():
    a = E.stream(3, 4, 4, E.bracket(), E.bracket())
    assert a == E.stream(3, 4, 4, E.bracket(), E.bracket())
    assert sum(1 for r in a if r["weekend"]) == 4
