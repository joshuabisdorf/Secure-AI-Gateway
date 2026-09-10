import json

import pytest

from app.evals import semantic_pii_benchmark as benchmark
from app.semantic_pii import SemanticPIIFinding


class KeywordAnalyzer:
    def analyze(self, text: str) -> tuple[SemanticPIIFinding, ...]:
        """
        RME

        Requires:
            - Test text may contain the marker PRIVATE.

        Modifies:
            - Nothing.

        Effects:
            - Supplies deterministic findings for benchmark-framework tests.

        Inputs:
            - text: Synthetic benchmark text.

        Outputs:
            - One person-name finding when PRIVATE appears, otherwise no findings.
        """
        start = text.find("PRIVATE")
        if start < 0:
            return ()
        return (SemanticPIIFinding("person_name", start, start + 7),)


def _minimal_document() -> dict[str, object]:
    """
    RME

    Requires:
        - Nothing.

    Modifies:
        - Nothing.

    Effects:
        - Builds a minimal valid semantic PII benchmark for parser/evaluator tests.

    Inputs:
        - None.

    Outputs:
        - Version-1 benchmark dictionary with one PII and one benign case.
    """
    return {
        "name": "test-semantic-pii",
        "version": 1,
        "baseline_thresholds": {
            "minimum_precision": 1.0,
            "minimum_recall": 1.0,
            "maximum_false_positive_rate": 0.0,
        },
        "cases": [
            {
                "id": "pii-001",
                "label": "pii",
                "category": "pii-test",
                "text": "PRIVATE",
                "expected_types": ["person_name"],
            },
            {
                "id": "benign-001",
                "label": "benign",
                "category": "benign-test",
                "text": "PUBLIC",
                "expected_types": [],
            },
        ],
    }


def test_semantic_pii_benchmark_framework_computes_perfect_metrics(monkeypatch) -> None:
    """
    RME

    Requires:
        - The minimal benchmark is valid and the deterministic analyzer is injected.

    Modifies:
        - Temporarily replaces the evaluator's semantic analyzer.

    Effects:
        - Verifies confusion metrics, type matching, and regression-gate behavior.

    Inputs:
        - monkeypatch: Pytest monkeypatch fixture.

    Outputs:
        - None. Assertions determine whether benchmark accounting is correct.
    """
    dataset = benchmark.parse_dataset(json.dumps(_minimal_document()), sha256="abc")
    monkeypatch.setattr(benchmark, "semantic_pii_analyzer", KeywordAnalyzer())

    report = benchmark.evaluate(dataset)

    assert report.metrics.true_positive == 1
    assert report.metrics.false_positive == 0
    assert report.metrics.true_negative == 1
    assert report.metrics.false_negative == 0
    assert report.metrics.precision == 1.0
    assert report.metrics.recall == 1.0
    assert report.type_mismatch_ids == ()
    assert report.baseline_passed is True


def test_semantic_pii_benchmark_rejects_unknown_case_fields() -> None:
    """
    RME

    Requires:
        - A benchmark case contains an unsupported schema field.

    Modifies:
        - Nothing.

    Effects:
        - Verifies dataset schema errors fail closed.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether strict parsing rejects the document.
    """
    document = _minimal_document()
    cases = document["cases"]
    assert isinstance(cases, list)
    case = cases[0]
    assert isinstance(case, dict)
    case["unexpected"] = True

    with pytest.raises(ValueError):
        benchmark.parse_dataset(json.dumps(document))


def test_committed_semantic_pii_dataset_is_versioned_and_balanced() -> None:
    """
    RME

    Requires:
        - The committed version-1 semantic PII dataset is present.

    Modifies:
        - Nothing.

    Effects:
        - Verifies dataset size, version, labels, and reproducibility digest metadata.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether the committed benchmark contract is intact.
    """
    dataset = benchmark.load_dataset()

    assert dataset.version == 1
    assert len(dataset.cases) == 52
    assert {case.label for case in dataset.cases} == {"pii", "benign"}
    assert dataset.sha256 != "unknown"
