import json
from types import SimpleNamespace

import pytest

import app.evals.prompt_injection_benchmark as benchmark
from app.evals.prompt_injection_benchmark import (
    BenchmarkCase,
    BenchmarkThresholds,
    PromptInjectionDataset,
    evaluate_benchmark,
    load_benchmark_dataset,
    parse_benchmark_dataset,
)


def test_committed_prompt_injection_benchmark_meets_baseline() -> None:
    """
    RME

    Requires:
        - The version-1 benchmark dataset is committed at its default repository path.

    Modifies:
        - Nothing.

    Effects:
        - Runs the complete deterministic detector benchmark offline.
        - Verifies the current detector does not regress below the committed baseline.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether benchmark coverage and thresholds hold.
    """
    dataset = load_benchmark_dataset()
    report = evaluate_benchmark(dataset)

    assert dataset.version == 1
    assert report.total_cases == 66
    assert report.baseline_passed is True
    assert report.metrics.precision >= dataset.thresholds.minimum_precision
    assert report.metrics.recall >= dataset.thresholds.minimum_recall
    assert (
        report.metrics.false_positive_rate
        <= dataset.thresholds.maximum_false_positive_rate
    )

    categories = {item.category: item for item in report.categories}
    expected_categories = {
        "benign_general",
        "benign_security_hard_negative",
        "instruction_override",
        "system_prompt_extraction",
        "role_impersonation",
        "safety_bypass",
        "secret_exfiltration",
        "encoded_payload",
        "typoglycemia",
        "indirect_injection",
        "multi_turn_split",
    }
    assert set(categories) == expected_categories
    assert categories["instruction_override"].detection_rate == 1.0
    assert categories["encoded_payload"].detection_rate == 1.0


def test_evaluate_benchmark_computes_confusion_metrics(monkeypatch) -> None:
    """
    RME

    Requires:
        - A synthetic dataset encodes expected detector decisions in message content.

    Modifies:
        - Temporarily replaces the detector function used by the evaluator.

    Effects:
        - Verifies TP/FP/TN/FN and derived metric calculations independently of detector rules.

    Inputs:
        - monkeypatch: Pytest fixture used to replace the detector deterministically.

    Outputs:
        - None. Assertions determine whether metric calculations are correct.
    """
    cases = (
        BenchmarkCase("tp", "attack", "attack_cat", ({"role": "user", "content": "detect"},)),
        BenchmarkCase("fn", "attack", "attack_cat", ({"role": "user", "content": "clean"},)),
        BenchmarkCase("fp", "benign", "benign_cat", ({"role": "user", "content": "detect"},)),
        BenchmarkCase("tn", "benign", "benign_cat", ({"role": "user", "content": "clean"},)),
    )
    dataset = PromptInjectionDataset(
        name="synthetic",
        version=1,
        sha256="abc",
        thresholds=BenchmarkThresholds(
            minimum_precision=0.5,
            minimum_recall=0.5,
            maximum_false_positive_rate=0.5,
        ),
        cases=cases,
    )

    def fake_detector(request):
        detected = request.messages[0].content == "detect"
        return SimpleNamespace(detected_count=1 if detected else 0)

    monkeypatch.setattr(benchmark, "inspect_prompt_injection", fake_detector)
    report = evaluate_benchmark(dataset)

    assert report.metrics.true_positive == 1
    assert report.metrics.false_positive == 1
    assert report.metrics.true_negative == 1
    assert report.metrics.false_negative == 1
    assert report.metrics.precision == 0.5
    assert report.metrics.recall == 0.5
    assert report.metrics.false_positive_rate == 0.5
    assert report.metrics.false_negative_rate == 0.5
    assert report.false_positive_ids == ("fp",)
    assert report.false_negative_ids == ("fn",)
    assert report.baseline_passed is True


def test_parse_benchmark_dataset_rejects_duplicate_case_ids() -> None:
    """
    RME

    Requires:
        - Dataset input contains duplicate case identifiers.

    Modifies:
        - Nothing.

    Effects:
        - Verifies versioned benchmark case identity is enforced strictly.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether duplicate IDs are rejected.
    """
    document = {
        "name": "duplicate-test",
        "version": 1,
        "baseline_thresholds": {
            "minimum_precision": 0.0,
            "minimum_recall": 0.0,
            "maximum_false_positive_rate": 1.0,
        },
        "cases": [
            {
                "id": "same-id",
                "label": "attack",
                "category": "attack",
                "messages": [{"role": "user", "content": "attack"}],
            },
            {
                "id": "same-id",
                "label": "benign",
                "category": "benign",
                "messages": [{"role": "user", "content": "benign"}],
            },
        ],
    }

    with pytest.raises(ValueError, match="duplicate_case_id"):
        parse_benchmark_dataset(json.dumps(document))
