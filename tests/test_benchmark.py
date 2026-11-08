from benchmarks.run import SCENARIOS, run_scenario, summarize


def test_benchmark_meets_quality_bar():
    s = summarize([run_scenario(sc, "rules") for sc in SCENARIOS])
    assert s["precision"] >= 0.95 and s["recall"] >= 0.95
    assert s["mode_accuracy"] >= 0.9 and s["correction_quality"] >= 0.9
