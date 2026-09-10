import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.models import ChatCompletionRequest
from app.prompt_injection import inspect_prompt_injection

_DEFAULT_DATASET = (
    Path(__file__).resolve().parents[2]
    / "evals"
    / "datasets"
    / "prompt_injection_v1.json"
)
_MAX_DATASET_BYTES = 2_097_152
_CASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,96}$")
_CATEGORY_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_LABELS = frozenset({"attack", "benign"})
_ROOT_KEYS = frozenset({"name", "version", "baseline_thresholds", "cases"})
_THRESHOLD_KEYS = frozenset(
    {"minimum_precision", "minimum_recall", "maximum_false_positive_rate"}
)
_CASE_KEYS = frozenset({"id", "label", "category", "messages"})
_MESSAGE_KEYS = frozenset({"role", "content"})


class BenchmarkDatasetError(RuntimeError):
    """Raised when a prompt-injection benchmark dataset cannot be used safely."""

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
    messages: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class PromptInjectionDataset:
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
    baseline_passed: bool


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for key, value in pairs:
        if key in parsed:
            raise ValueError("duplicate_json_key")
        parsed[key] = value
    return parsed


def _require_exact_keys(
    value: dict[str, Any],
    expected: frozenset[str],
    reason: str,
) -> None:
    if frozenset(value) != expected:
        raise ValueError(reason)


def _parse_unit_interval(value: Any, reason: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(reason)
    parsed = float(value)
    if parsed < 0 or parsed > 1:
        raise ValueError(reason)
    return parsed


def _parse_thresholds(value: Any) -> BenchmarkThresholds:
    if not isinstance(value, dict):
        raise ValueError("invalid_benchmark_thresholds")
    _require_exact_keys(value, _THRESHOLD_KEYS, "invalid_benchmark_thresholds")
    return BenchmarkThresholds(
        minimum_precision=_parse_unit_interval(
            value["minimum_precision"], "invalid_minimum_precision"
        ),
        minimum_recall=_parse_unit_interval(
            value["minimum_recall"], "invalid_minimum_recall"
        ),
        maximum_false_positive_rate=_parse_unit_interval(
            value["maximum_false_positive_rate"],
            "invalid_maximum_false_positive_rate",
        ),
    )


def _parse_case(value: Any, seen_ids: set[str]) -> BenchmarkCase:
    if not isinstance(value, dict):
        raise ValueError("invalid_benchmark_case")
    _require_exact_keys(value, _CASE_KEYS, "invalid_benchmark_case")

    case_id = value["id"]
    label = value["label"]
    category = value["category"]
    messages = value["messages"]

    if not isinstance(case_id, str) or not _CASE_ID_PATTERN.fullmatch(case_id):
        raise ValueError("invalid_case_id")
    if case_id in seen_ids:
        raise ValueError("duplicate_case_id")
    seen_ids.add(case_id)

    if label not in _LABELS:
        raise ValueError("invalid_case_label")
    if not isinstance(category, str) or not _CATEGORY_PATTERN.fullmatch(category):
        raise ValueError("invalid_case_category")
    if not isinstance(messages, list) or not messages or len(messages) > 16:
        raise ValueError("invalid_case_messages")

    parsed_messages: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("invalid_case_message")
        _require_exact_keys(message, _MESSAGE_KEYS, "invalid_case_message")
        role = message["role"]
        content = message["content"]
        if not isinstance(role, str) or not role or len(role) > 32:
            raise ValueError("invalid_case_message_role")
        if not isinstance(content, str) or not content or len(content) > 16_384:
            raise ValueError("invalid_case_message_content")
        parsed_messages.append({"role": role, "content": content})

    return BenchmarkCase(
        case_id=case_id,
        label=label,
        category=category,
        messages=tuple(parsed_messages),
    )


def parse_benchmark_dataset(document: str, *, sha256: str = "unknown") -> PromptInjectionDataset:
    """
    RME

    Requires:
        - document is intended to be a version-1 prompt-injection benchmark dataset.

    Modifies:
        - Nothing.

    Effects:
        - Strictly validates dataset structure, thresholds, unique IDs, labels, and messages.
        - Rejects categories that mix attack and benign labels so per-category rates remain clear.

    Inputs:
        - document: UTF-8 JSON benchmark document.
        - sha256: Digest of the raw dataset bytes when available.

    Outputs:
        - Validated PromptInjectionDataset.

    Raises:
        - ValueError: Dataset content is malformed or violates the benchmark schema.
    """
    try:
        root = json.loads(document, object_pairs_hook=_object_without_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_benchmark_json") from exc

    if not isinstance(root, dict):
        raise ValueError("invalid_benchmark_root")
    _require_exact_keys(root, _ROOT_KEYS, "invalid_benchmark_root")

    name = root["name"]
    version = root["version"]
    if not isinstance(name, str) or not name or len(name) > 96:
        raise ValueError("invalid_benchmark_name")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise ValueError("unsupported_benchmark_version")

    raw_cases = root["cases"]
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("no_benchmark_cases")

    seen_ids: set[str] = set()
    cases = tuple(_parse_case(value, seen_ids) for value in raw_cases)
    labels = {case.label for case in cases}
    if labels != _LABELS:
        raise ValueError("benchmark_requires_attack_and_benign_cases")

    category_labels: dict[str, str] = {}
    for case in cases:
        existing = category_labels.setdefault(case.category, case.label)
        if existing != case.label:
            raise ValueError("benchmark_category_mixes_labels")

    return PromptInjectionDataset(
        name=name,
        version=version,
        sha256=sha256,
        thresholds=_parse_thresholds(root["baseline_thresholds"]),
        cases=cases,
    )


def load_benchmark_dataset(path: Path = _DEFAULT_DATASET) -> PromptInjectionDataset:
    """
    RME

    Requires:
        - path identifies a local benchmark JSON file no larger than 2 MiB.

    Modifies:
        - Nothing.

    Effects:
        - Reads the dataset, computes its SHA-256 digest, and validates its complete schema.

    Inputs:
        - path: Dataset path; defaults to the committed version-1 benchmark.

    Outputs:
        - Validated PromptInjectionDataset with reproducibility digest.

    Raises:
        - BenchmarkDatasetError: File is unavailable, oversized, undecodable, or invalid.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BenchmarkDatasetError("benchmark_dataset_unavailable") from exc

    if len(raw) > _MAX_DATASET_BYTES:
        raise BenchmarkDatasetError("benchmark_dataset_too_large")

    digest = hashlib.sha256(raw).hexdigest()
    try:
        document = raw.decode("utf-8")
        return parse_benchmark_dataset(document, sha256=digest)
    except (UnicodeError, ValueError) as exc:
        raise BenchmarkDatasetError("invalid_benchmark_dataset") from exc


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def evaluate_benchmark(dataset: PromptInjectionDataset) -> BenchmarkReport:
    """
    RME

    Requires:
        - dataset has passed benchmark schema validation.

    Modifies:
        - Nothing.

    Effects:
        - Runs the deterministic prompt-injection detector against every benchmark case.
        - Computes confusion-matrix metrics and per-category detection rates.
        - Retains only case IDs for errors; prompt bodies are not copied into the report.

    Inputs:
        - dataset: Validated benchmark dataset.

    Outputs:
        - BenchmarkReport with overall/category metrics and baseline status.
    """
    true_positive = false_positive = true_negative = false_negative = 0
    false_positive_ids: list[str] = []
    false_negative_ids: list[str] = []
    category_counts: dict[str, list[Any]] = {}

    for case in dataset.cases:
        request = ChatCompletionRequest(
            model="benchmark-model",
            messages=list(case.messages),
        )
        detected = inspect_prompt_injection(request).detected_count > 0

        counts = category_counts.setdefault(case.category, [case.label, 0, 0])
        counts[1] += 1
        counts[2] += int(detected)

        if case.label == "attack":
            if detected:
                true_positive += 1
            else:
                false_negative += 1
                false_negative_ids.append(case.case_id)
        elif detected:
            false_positive += 1
            false_positive_ids.append(case.case_id)
        else:
            true_negative += 1

    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    false_positive_rate = _ratio(false_positive, false_positive + true_negative)
    false_negative_rate = _ratio(false_negative, true_positive + false_negative)

    metrics = ConfusionMetrics(
        true_positive=true_positive,
        false_positive=false_positive,
        true_negative=true_negative,
        false_negative=false_negative,
        precision=precision,
        recall=recall,
        false_positive_rate=false_positive_rate,
        false_negative_rate=false_negative_rate,
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
        precision >= thresholds.minimum_precision
        and recall >= thresholds.minimum_recall
        and false_positive_rate <= thresholds.maximum_false_positive_rate
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
        baseline_passed=baseline_passed,
    )


def _report_as_dict(report: BenchmarkReport, *, show_errors: bool) -> dict[str, Any]:
    metrics = report.metrics
    payload: dict[str, Any] = {
        "dataset": {
            "name": report.dataset_name,
            "version": report.dataset_version,
            "sha256": report.dataset_sha256,
            "cases": report.total_cases,
        },
        "overall": {
            "true_positive": metrics.true_positive,
            "false_positive": metrics.false_positive,
            "true_negative": metrics.true_negative,
            "false_negative": metrics.false_negative,
            "precision": metrics.precision,
            "recall": metrics.recall,
            "false_positive_rate": metrics.false_positive_rate,
            "false_negative_rate": metrics.false_negative_rate,
        },
        "categories": [
            {
                "category": item.category,
                "label": item.label,
                "cases": item.cases,
                "detected": item.detected,
                "detection_rate": item.detection_rate,
            }
            for item in report.categories
        ],
        "baseline_passed": report.baseline_passed,
    }
    if show_errors:
        payload["false_positive_ids"] = list(report.false_positive_ids)
        payload["false_negative_ids"] = list(report.false_negative_ids)
    return payload


def _print_text(report: BenchmarkReport, *, show_errors: bool) -> None:
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
        print(
            "FALSE_POSITIVES ids="
            + (",".join(report.false_positive_ids) or "none")
        )
        print(
            "FALSE_NEGATIVES ids="
            + (",".join(report.false_negative_ids) or "none")
        )
    print(f"BASELINE status={'PASS' if report.baseline_passed else 'FAIL'}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the deterministic prompt-injection detector offline."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=_DEFAULT_DATASET,
        help="Benchmark JSON file; defaults to the committed version-1 dataset.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format. Neither format includes prompt bodies.",
    )
    parser.add_argument(
        "--show-errors",
        action="store_true",
        help="Include false-positive/false-negative case IDs without prompt bodies.",
    )
    parser.add_argument(
        "--enforce-baseline",
        action="store_true",
        help="Exit 1 when committed precision/recall/FPR thresholds are not met.",
    )
    return parser


def main() -> None:
    """
    RME

    Requires:
        - The selected benchmark dataset is locally readable and valid.

    Modifies:
        - Terminal output only.

    Effects:
        - Runs an offline reproducible detector evaluation with no provider/network calls.
        - Exits 2 for dataset errors and, when --enforce-baseline is set, 1 for regression.

    Inputs:
        - Command-line arguments.

    Outputs:
        - Text or JSON benchmark metrics without raw benchmark prompt bodies.
    """
    args = _build_parser().parse_args()
    try:
        dataset = load_benchmark_dataset(args.dataset)
    except BenchmarkDatasetError as exc:
        print(f"ERROR benchmark reason={exc.reason}")
        raise SystemExit(2) from None

    report = evaluate_benchmark(dataset)
    if args.format == "json":
        print(json.dumps(_report_as_dict(report, show_errors=args.show_errors), sort_keys=True))
    else:
        _print_text(report, show_errors=args.show_errors)

    if args.enforce_baseline and not report.baseline_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
