import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.semantic_pii import SemanticPIIUnavailable, semantic_pii_analyzer

_DEFAULT_DATASET = (
    Path(__file__).resolve().parents[2] / "evals" / "datasets" / "semantic_pii_v1.json"
)
_MAX_DATASET_BYTES = 2_097_152
_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,96}$")
_CATEGORY_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_LABELS = frozenset({"pii", "benign"})
_ROOT_KEYS = frozenset({"name", "version", "baseline_thresholds", "cases"})
_THRESHOLD_KEYS = frozenset(
    {"minimum_precision", "minimum_recall", "maximum_false_positive_rate"}
)
_CASE_KEYS = frozenset({"id", "label", "category", "text", "expected_types"})


class SemanticPIIBenchmarkError(RuntimeError):
    """Raised when a semantic PII benchmark cannot be evaluated safely."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class BenchmarkThresholds:
    minimum_precision: float
    minimum_recall: float
    maximum_false_positive_rate: float


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    label: str
    category: str
    text: str
    expected_types: tuple[str, ...]


@dataclass(frozen=True)
class SemanticPIIDataset:
    name: str
    version: int
    sha256: str
    thresholds: BenchmarkThresholds
    cases: tuple[BenchmarkCase, ...]


@dataclass(frozen=True)
class ConfusionMetrics:
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int
    precision: float
    recall: float
    false_positive_rate: float
    false_negative_rate: float


@dataclass(frozen=True)
class CategoryMetrics:
    category: str
    label: str
    cases: int
    detected: int
    detection_rate: float


@dataclass(frozen=True)
class BenchmarkReport:
    dataset_name: str
    dataset_version: int
    dataset_sha256: str
    total_cases: int
    metrics: ConfusionMetrics
    categories: tuple[CategoryMetrics, ...]
    false_positive_ids: tuple[str, ...]
    false_negative_ids: tuple[str, ...]
    type_mismatch_ids: tuple[str, ...]
    baseline_passed: bool


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON keys while constructing benchmark objects."""
    parsed: dict[str, Any] = {}
    for key, value in pairs:
        if key in parsed:
            raise ValueError("duplicate_json_key")
        parsed[key] = value
    return parsed


def _require_exact_keys(value: dict[str, Any], expected: frozenset[str], reason: str) -> None:
    """Reject missing or unknown schema fields."""
    if frozenset(value) != expected:
        raise ValueError(reason)


def _parse_unit_interval(value: Any, reason: str) -> float:
    """Parse a numeric threshold in the inclusive interval [0, 1]."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(reason)
    parsed = float(value)
    if parsed < 0 or parsed > 1:
        raise ValueError(reason)
    return parsed


def parse_dataset(document: str, *, sha256: str = "unknown") -> SemanticPIIDataset:
    """
    RME

    Requires:
        - document is intended to be a version-1 semantic PII benchmark.

    Modifies:
        - Nothing.

    Effects:
        - Strictly validates benchmark schema, IDs, labels, expected types, and thresholds.
        - Requires categories to contain only one label so category detection rates are interpretable.

    Inputs:
        - document: UTF-8 JSON benchmark document.
        - sha256: Digest of the raw document when available.

    Outputs:
        - Validated SemanticPIIDataset.
    """
    try:
        root = json.loads(document, object_pairs_hook=_object_without_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_semantic_pii_benchmark_json") from exc
    if not isinstance(root, dict):
        raise ValueError("invalid_semantic_pii_benchmark_root")
    _require_exact_keys(root, _ROOT_KEYS, "invalid_semantic_pii_benchmark_root")

    name = root["name"]
    version = root["version"]
    if not isinstance(name, str) or not name or len(name) > 96:
        raise ValueError("invalid_benchmark_name")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise ValueError("unsupported_benchmark_version")

    raw_thresholds = root["baseline_thresholds"]
    if not isinstance(raw_thresholds, dict):
        raise ValueError("invalid_benchmark_thresholds")
    _require_exact_keys(raw_thresholds, _THRESHOLD_KEYS, "invalid_benchmark_thresholds")
    thresholds = BenchmarkThresholds(
        minimum_precision=_parse_unit_interval(
            raw_thresholds["minimum_precision"], "invalid_minimum_precision"
        ),
        minimum_recall=_parse_unit_interval(
            raw_thresholds["minimum_recall"], "invalid_minimum_recall"
        ),
        maximum_false_positive_rate=_parse_unit_interval(
            raw_thresholds["maximum_false_positive_rate"],
            "invalid_maximum_false_positive_rate",
        ),
    )

    raw_cases = root["cases"]
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("no_benchmark_cases")

    seen_ids: set[str] = set()
    category_labels: dict[str, str] = {}
    cases: list[BenchmarkCase] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise ValueError("invalid_benchmark_case")
        _require_exact_keys(raw_case, _CASE_KEYS, "invalid_benchmark_case")
        case_id = raw_case["id"]
        label = raw_case["label"]
        category = raw_case["category"]
        text = raw_case["text"]
        expected_types = raw_case["expected_types"]

        if not isinstance(case_id, str) or not _ID_PATTERN.fullmatch(case_id):
            raise ValueError("invalid_case_id")
        if case_id in seen_ids:
            raise ValueError("duplicate_case_id")
        seen_ids.add(case_id)
        if not isinstance(label, str) or label not in _LABELS:
            raise ValueError("invalid_case_label")
        if not isinstance(category, str) or not _CATEGORY_PATTERN.fullmatch(category):
            raise ValueError("invalid_case_category")
        if not isinstance(text, str) or not text or len(text) > 16_384:
            raise ValueError("invalid_case_text")
        if not isinstance(expected_types, list) or any(
            not isinstance(item, str) or not item for item in expected_types
        ):
            raise ValueError("invalid_expected_types")
        if len(set(expected_types)) != len(expected_types):
            raise ValueError("duplicate_expected_type")
        if label == "pii" and not expected_types:
            raise ValueError("pii_case_requires_expected_type")
        if label == "benign" and expected_types:
            raise ValueError("benign_case_cannot_expect_pii")

        existing_label = category_labels.setdefault(category, label)
        if existing_label != label:
            raise ValueError("benchmark_category_mixes_labels")
        cases.append(
            BenchmarkCase(
                case_id=case_id,
                label=label,
                category=category,
                text=text,
                expected_types=tuple(expected_types),
            )
        )

    if {case.label for case in cases} != _LABELS:
        raise ValueError("benchmark_requires_pii_and_benign_cases")

    return SemanticPIIDataset(
        name=name,
        version=version,
        sha256=sha256,
        thresholds=thresholds,
        cases=tuple(cases),
    )


def load_dataset(path: Path = _DEFAULT_DATASET) -> SemanticPIIDataset:
    """
    RME

    Requires:
        - path identifies a UTF-8 JSON dataset no larger than 2 MiB.

    Modifies:
        - Nothing.

    Effects:
        - Reads the dataset, computes SHA-256, and validates its full schema.

    Inputs:
        - path: Dataset path.

    Outputs:
        - Validated semantic PII dataset.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SemanticPIIBenchmarkError("semantic_pii_dataset_unavailable") from exc
    if len(raw) > _MAX_DATASET_BYTES:
        raise SemanticPIIBenchmarkError("semantic_pii_dataset_too_large")
    digest = hashlib.sha256(raw).hexdigest()
    try:
        return parse_dataset(raw.decode("utf-8"), sha256=digest)
    except (UnicodeError, ValueError) as exc:
        raise SemanticPIIBenchmarkError("invalid_semantic_pii_dataset") from exc


def _ratio(numerator: int, denominator: int) -> float:
    """Return a safe ratio, using zero when the denominator is zero."""
    return 0.0 if denominator == 0 else numerator / denominator


def evaluate(dataset: SemanticPIIDataset) -> BenchmarkReport:
    """
    RME

    Requires:
        - dataset has passed schema validation.
        - The configured local semantic PII model is available.

    Modifies:
        - Lazy process-local NLP model state on first use.

    Effects:
        - Evaluates every case without sending text to a network service.
        - Computes case-level confusion metrics and per-category detection rates.
        - Records only case IDs for failures and never includes benchmark text in the report.

    Inputs:
        - dataset: Validated semantic PII benchmark.

    Outputs:
        - BenchmarkReport with metrics, category results, safe error IDs, and baseline status.
    """
    tp = fp = tn = fn = 0
    false_positive_ids: list[str] = []
    false_negative_ids: list[str] = []
    type_mismatch_ids: list[str] = []
    category_counts: dict[str, list[Any]] = {}

    for case in dataset.cases:
        findings = semantic_pii_analyzer.analyze(case.text)
        detected_types = {finding.pii_type for finding in findings}
        detected = bool(findings)
        counts = category_counts.setdefault(case.category, [case.label, 0, 0])
        counts[1] += 1
        counts[2] += int(detected)

        if case.label == "pii":
            if detected:
                tp += 1
                if not set(case.expected_types).issubset(detected_types):
                    type_mismatch_ids.append(case.case_id)
            else:
                fn += 1
                false_negative_ids.append(case.case_id)
        elif detected:
            fp += 1
            false_positive_ids.append(case.case_id)
        else:
            tn += 1

    metrics = ConfusionMetrics(
        true_positive=tp,
        false_positive=fp,
        true_negative=tn,
        false_negative=fn,
        precision=_ratio(tp, tp + fp),
        recall=_ratio(tp, tp + fn),
        false_positive_rate=_ratio(fp, fp + tn),
        false_negative_rate=_ratio(fn, tp + fn),
    )
    categories = tuple(
        CategoryMetrics(
            category=category,
            label=str(values[0]),
            cases=int(values[1]),
            detected=int(values[2]),
            detection_rate=_ratio(int(values[2]), int(values[1])),
        )
        for category, values in sorted(category_counts.items())
    )
    thresholds = dataset.thresholds
    baseline_passed = (
        metrics.precision >= thresholds.minimum_precision
        and metrics.recall >= thresholds.minimum_recall
        and metrics.false_positive_rate <= thresholds.maximum_false_positive_rate
    )
    return BenchmarkReport(
        dataset_name=dataset.name,
        dataset_version=dataset.version,
        dataset_sha256=dataset.sha256,
        total_cases=len(dataset.cases),
        metrics=metrics,
        categories=categories,
        false_positive_ids=tuple(false_positive_ids),
        false_negative_ids=tuple(false_negative_ids),
        type_mismatch_ids=tuple(type_mismatch_ids),
        baseline_passed=baseline_passed,
    )


def _print_text(report: BenchmarkReport, *, show_errors: bool) -> None:
    """Print metadata-only benchmark results without source text."""
    metrics = report.metrics
    print(
        f"DATASET name={report.dataset_name} version={report.dataset_version} "
        f"cases={report.total_cases} sha256={report.dataset_sha256}"
    )
    print(
        "OVERALL "
        f"tp={metrics.true_positive} fp={metrics.false_positive} "
        f"tn={metrics.true_negative} fn={metrics.false_negative} "
        f"precision={metrics.precision:.4f} recall={metrics.recall:.4f} "
        f"false_positive_rate={metrics.false_positive_rate:.4f} "
        f"false_negative_rate={metrics.false_negative_rate:.4f}"
    )
    for item in report.categories:
        print(
            f"CATEGORY name={item.category} label={item.label} cases={item.cases} "
            f"detected={item.detected} detection_rate={item.detection_rate:.4f}"
        )
    if show_errors:
        print("FALSE_POSITIVES ids=" + (",".join(report.false_positive_ids) or "none"))
        print("FALSE_NEGATIVES ids=" + (",".join(report.false_negative_ids) or "none"))
        print("TYPE_MISMATCHES ids=" + (",".join(report.type_mismatch_ids) or "none"))
    print(f"BASELINE status={'PASS' if report.baseline_passed else 'FAIL'}")


def _report_dict(report: BenchmarkReport, *, show_errors: bool) -> dict[str, Any]:
    """Convert a report to machine-readable metadata without benchmark text."""
    payload: dict[str, Any] = {
        "dataset": {
            "name": report.dataset_name,
            "version": report.dataset_version,
            "sha256": report.dataset_sha256,
            "cases": report.total_cases,
        },
        "overall": report.metrics.__dict__,
        "categories": [item.__dict__ for item in report.categories],
        "baseline_passed": report.baseline_passed,
    }
    if show_errors:
        payload.update(
            {
                "false_positive_ids": list(report.false_positive_ids),
                "false_negative_ids": list(report.false_negative_ids),
                "type_mismatch_ids": list(report.type_mismatch_ids),
            }
        )
    return payload


def main() -> None:
    """
    RME

    Requires:
        - The local semantic PII model and selected dataset are available.

    Modifies:
        - Terminal output and lazy process-local NLP state.

    Effects:
        - Runs the semantic PII benchmark offline.
        - Exits 1 when --enforce-baseline is supplied and thresholds are missed.
        - Exits 2 when the benchmark/model cannot be evaluated safely.

    Inputs:
        - Command-line options.

    Outputs:
        - Text or JSON metrics containing no benchmark prompt bodies.
    """
    parser = argparse.ArgumentParser(description="Evaluate semantic PII detection offline.")
    parser.add_argument("--dataset", type=Path, default=_DEFAULT_DATASET)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--show-errors", action="store_true")
    parser.add_argument("--enforce-baseline", action="store_true")
    args = parser.parse_args()

    try:
        report = evaluate(load_dataset(args.dataset))
    except (SemanticPIIBenchmarkError, SemanticPIIUnavailable) as exc:
        reason = getattr(exc, "reason", str(exc))
        print(f"ERROR semantic_pii_benchmark reason={reason}")
        raise SystemExit(2) from None

    if args.format == "json":
        print(json.dumps(_report_dict(report, show_errors=args.show_errors), sort_keys=True))
    else:
        _print_text(report, show_errors=args.show_errors)

    if args.enforce_baseline and not report.baseline_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
