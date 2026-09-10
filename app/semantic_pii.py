import os
import re
import threading
from dataclasses import dataclass
from typing import Protocol

import spacy
from fastapi import HTTPException, status
from spacy.language import Language

_DEFAULT_MODEL = "en_core_web_sm"
_SUPPORTED_BACKENDS = frozenset({"spacy"})
_CONTEXT_RADIUS = 96

_person_context_patterns = (
    re.compile(r"\bmy\s+name\s+is\b", re.IGNORECASE),
    re.compile(r"\b(?:i\s+am|i['’]m|call\s+me)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:contact|customer|patient|employee|recipient|sender|client|account\s+holder)\b",
        re.IGNORECASE,
    ),
)
_location_context_patterns = (
    re.compile(r"\b(?:i|we|she|he|they)\s+(?:live|reside)\b", re.IGNORECASE),
    re.compile(r"\b(?:lives|resides|based|located)\s+in\b", re.IGNORECASE),
    re.compile(r"\b(?:my|home|billing|shipping)\s+address\b", re.IGNORECASE),
    re.compile(r"\bborn\s+in\b", re.IGNORECASE),
)
_dob_context_patterns = (
    re.compile(r"\bdate\s+of\s+birth\b", re.IGNORECASE),
    re.compile(r"\bDOB\b", re.IGNORECASE),
    re.compile(r"\bborn\s+(?:on\s+)?\b", re.IGNORECASE),
    re.compile(r"\bbirthday\b", re.IGNORECASE),
)
_street_address_pattern = re.compile(
    r"(?<!\w)\d{1,6}\s+"
    r"(?:[A-Za-z0-9][A-Za-z0-9.'-]*\s+){0,5}"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|"
    r"Court|Ct|Way|Parkway|Pkwy|Circle|Cir|Trail|Trl|Terrace|Ter)\b"
    r"(?:\s+(?:Apt|Apartment|Unit|Suite|#)\s*[A-Za-z0-9-]+)?",
    re.IGNORECASE,
)
_replacements = {
    "person_name": "[REDACTED_PERSON]",
    "personal_location": "[REDACTED_LOCATION]",
    "date_of_birth": "[REDACTED_DOB]",
    "street_address": "[REDACTED_ADDRESS]",
}
_priority = {
    "street_address": 0,
    "date_of_birth": 1,
    "person_name": 2,
    "personal_location": 3,
}


class SemanticPIIUnavailable(HTTPException):
    """Fail-closed HTTP error for an unavailable semantic PII analyzer."""

    def __init__(self, reason: str) -> None:
        """
        RME

        Requires:
            - reason is a safe internal reason label and contains no prompt data.

        Modifies:
            - Initializes exception state.

        Effects:
            - Represents semantic PII unavailability as a sanitized HTTP 503 response.

        Inputs:
            - reason: Safe diagnostic reason label.

        Outputs:
            - Configured SemanticPIIUnavailable exception instance.
        """
        self.reason = reason
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PII detection is unavailable.",
        )


@dataclass(frozen=True)
class SemanticPIIFinding:
    pii_type: str
    start: int
    end: int


class SemanticPIIAnalyzer(Protocol):
    def analyze(self, text: str) -> tuple[SemanticPIIFinding, ...]:
        """Return non-overlapping semantic PII spans without retaining raw values."""
        ...


def _context_window(text: str, start: int, end: int) -> str:
    """
    RME

    Requires:
        - start/end identify a span within text.

    Modifies:
        - Nothing.

    Effects:
        - Limits context inspection around an NLP entity to a bounded local window.

    Inputs:
        - text: Source message text.
        - start: Entity start offset.
        - end: Entity end offset.

    Outputs:
        - Bounded text surrounding the entity.
    """
    return text[max(0, start - _CONTEXT_RADIUS): min(len(text), end + _CONTEXT_RADIUS)]


def _has_context(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    """
    RME

    Requires:
        - patterns contains compiled contextual indicators.

    Modifies:
        - Nothing.

    Effects:
        - Tests whether any contextual indicator occurs in bounded text.

    Inputs:
        - text: Context window.
        - patterns: Allowed contextual patterns.

    Outputs:
        - True when at least one context pattern matches.
    """
    return any(pattern.search(text) for pattern in patterns)


def _select_non_overlapping(
    findings: list[SemanticPIIFinding],
) -> tuple[SemanticPIIFinding, ...]:
    """
    RME

    Requires:
        - findings contains offsets relative to one source string.

    Modifies:
        - Nothing.

    Effects:
        - Resolves overlapping recognizer results deterministically.
        - Prefers longer spans and higher-priority privacy types.

    Inputs:
        - findings: Candidate semantic/hybrid PII spans.

    Outputs:
        - Sorted non-overlapping findings safe for right-to-left redaction.
    """
    ordered = sorted(
        findings,
        key=lambda item: (
            item.start,
            -(item.end - item.start),
            _priority.get(item.pii_type, 99),
        ),
    )
    selected: list[SemanticPIIFinding] = []
    for candidate in ordered:
        if any(
            candidate.start < existing.end and candidate.end > existing.start
            for existing in selected
        ):
            continue
        selected.append(candidate)
    return tuple(sorted(selected, key=lambda item: (item.start, item.end)))


class SpacySemanticPIIAnalyzer:
    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        """
        RME

        Requires:
            - model_name identifies an installed spaCy English NER pipeline.

        Modifies:
            - Initializes lazy model state and synchronization primitives.

        Effects:
            - Defers expensive NLP model loading until semantic inspection is needed.

        Inputs:
            - model_name: Installed spaCy model package name.

        Outputs:
            - Configured analyzer instance.
        """
        self._model_name = model_name
        self._nlp: Language | None = None
        self._load_lock = threading.Lock()

    def _get_nlp(self) -> Language:
        """
        RME

        Requires:
            - The configured spaCy model is installed and loadable.

        Modifies:
            - Lazy process-local NLP model state on first use.

        Effects:
            - Loads only the components required for NER.
            - Converts model-loading failures into a sanitized fail-closed 503 error.

        Inputs:
            - None.

        Outputs:
            - Loaded spaCy Language pipeline.
        """
        if self._nlp is not None:
            return self._nlp
        with self._load_lock:
            if self._nlp is not None:
                return self._nlp
            try:
                self._nlp = spacy.load(
                    self._model_name,
                    disable=["tagger", "parser", "attribute_ruler", "lemmatizer"],
                )
            except Exception as exc:
                raise SemanticPIIUnavailable("semantic_pii_model_unavailable") from exc
        return self._nlp

    def analyze(self, text: str) -> tuple[SemanticPIIFinding, ...]:
        """
        RME

        Requires:
            - text is one message after deterministic structured PII redaction.

        Modifies:
            - Lazy spaCy model state on first call.

        Effects:
            - Uses local NER plus bounded context to identify person names, personal locations, and dates of birth.
            - Adds a complementary street-address recognizer.
            - Returns offsets/types only and never persists raw matched values.

        Inputs:
            - text: Sanitized message text to inspect.

        Outputs:
            - Non-overlapping semantic PII findings.
        """
        findings: list[SemanticPIIFinding] = []
        for match in _street_address_pattern.finditer(text):
            findings.append(
                SemanticPIIFinding("street_address", match.start(), match.end())
            )

        doc = self._get_nlp()(text)
        for entity in doc.ents:
            context = _context_window(text, entity.start_char, entity.end_char)
            if entity.label_ == "PERSON" and _has_context(context, _person_context_patterns):
                findings.append(
                    SemanticPIIFinding("person_name", entity.start_char, entity.end_char)
                )
            elif entity.label_ in {"GPE", "LOC", "FAC"} and _has_context(
                context, _location_context_patterns
            ):
                findings.append(
                    SemanticPIIFinding(
                        "personal_location", entity.start_char, entity.end_char
                    )
                )
            elif entity.label_ == "DATE" and _has_context(context, _dob_context_patterns):
                findings.append(
                    SemanticPIIFinding("date_of_birth", entity.start_char, entity.end_char)
                )

        return _select_non_overlapping(findings)


def redact_semantic_text(
    text: str,
    analyzer: SemanticPIIAnalyzer,
) -> tuple[str, tuple[str, ...]]:
    """
    RME

    Requires:
        - analyzer returns valid, non-overlapping offsets into text.

    Modifies:
        - Nothing.

    Effects:
        - Replaces semantic PII spans from right to left so offsets remain valid.
        - Does not return or retain raw detected values separately.

    Inputs:
        - text: Message text after structured PII redaction.
        - analyzer: Semantic analyzer implementation.

    Outputs:
        - Redacted text and one safe PII type label per detected span.
    """
    findings = analyzer.analyze(text)
    redacted = text
    for finding in reversed(findings):
        replacement = _replacements[finding.pii_type]
        redacted = redacted[: finding.start] + replacement + redacted[finding.end :]
    return redacted, tuple(finding.pii_type for finding in findings)


def build_semantic_pii_analyzer() -> SemanticPIIAnalyzer:
    """
    RME

    Requires:
        - SAG_SEMANTIC_PII_BACKEND may select a supported local analyzer backend.

    Modifies:
        - Nothing.

    Effects:
        - Constructs the configured semantic PII analyzer without loading its model yet.
        - Rejects unsupported backends instead of silently disabling semantic inspection.

    Inputs:
        - None.

    Outputs:
        - Configured SemanticPIIAnalyzer.
    """
    backend = os.getenv("SAG_SEMANTIC_PII_BACKEND", "spacy").strip().lower()
    if backend not in _SUPPORTED_BACKENDS:
        raise SemanticPIIUnavailable("unsupported_semantic_pii_backend")
    return SpacySemanticPIIAnalyzer()


semantic_pii_analyzer = build_semantic_pii_analyzer()
