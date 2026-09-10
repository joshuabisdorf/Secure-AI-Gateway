from fastapi import HTTPException

from app.models import ChatCompletionRequest
from app.pii import inspect_and_redact_request
from app.semantic_pii import (
    SemanticPIIFinding,
    SpacySemanticPIIAnalyzer,
    redact_semantic_text,
)


class FixedSemanticAnalyzer:
    def analyze(self, text: str) -> tuple[SemanticPIIFinding, ...]:
        """
        RME

        Requires:
            - text contains the fictional test name Jordan Lee.

        Modifies:
            - Nothing.

        Effects:
            - Supplies a deterministic semantic finding without loading an NLP model.

        Inputs:
            - text: Test message after structured redaction.

        Outputs:
            - One person-name finding when the test name is present.
        """
        marker = "Jordan Lee"
        start = text.find(marker)
        if start < 0:
            return ()
        return (SemanticPIIFinding("person_name", start, start + len(marker)),)


def test_structured_and_semantic_pii_are_redacted_in_one_copied_request() -> None:
    """
    RME

    Requires:
        - Structured PII and a deterministic semantic finding appear in one request.

    Modifies:
        - Nothing.

    Effects:
        - Verifies structured redaction runs before semantic redaction.
        - Verifies the original request remains unchanged and raw values are absent downstream.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether combined PII redaction is correct.
    """
    raw_content = "My name is Jordan Lee and my email is jordan@example.com."
    request = ChatCompletionRequest(
        model="mock-model",
        messages=[{"role": "user", "content": raw_content}],
    )

    result = inspect_and_redact_request(request, FixedSemanticAnalyzer())
    sanitized = result.redacted_request.messages[0].content

    assert request.messages[0].content == raw_content
    assert result.detected_count == 2
    assert result.detected_types == ("email", "person_name")
    assert sanitized is not None
    assert "Jordan Lee" not in sanitized
    assert "jordan@example.com" not in sanitized
    assert "[REDACTED_PERSON]" in sanitized
    assert "[REDACTED_EMAIL]" in sanitized


def test_spacy_semantic_layer_detects_street_address_without_network() -> None:
    """
    RME

    Requires:
        - The pinned local spaCy model is installed with the project.

    Modifies:
        - Lazy process-local spaCy model state.

    Effects:
        - Verifies the runtime semantic analyzer can load locally and redact a supported address.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether local semantic PII processing is operational.
    """
    analyzer = SpacySemanticPIIAnalyzer()
    text = "My address is 742 Evergreen Terrace."
    redacted, types = redact_semantic_text(text, analyzer)

    assert "742 Evergreen Terrace" not in redacted
    assert "[REDACTED_ADDRESS]" in redacted
    assert "street_address" in types


def test_missing_semantic_model_fails_closed_with_sanitized_503() -> None:
    """
    RME

    Requires:
        - A deliberately nonexistent spaCy model name is used.

    Modifies:
        - Temporary analyzer lazy-load state only.

    Effects:
        - Verifies model-loading failure becomes a sanitized fail-closed HTTP response.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether failure handling is safe.
    """
    analyzer = SpacySemanticPIIAnalyzer("sag_nonexistent_semantic_pii_model")

    try:
        analyzer.analyze("My name is Jordan Lee.")
    except HTTPException as exc:
        assert exc.status_code == 503
        assert exc.detail == "PII detection is unavailable."
    else:
        raise AssertionError("missing semantic model did not fail closed")
