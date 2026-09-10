import asyncio
import base64

from app.evals.system_prompt_leakage import (
    build_synthetic_system_prompt,
    detect_system_prompt_leakage,
    run_live_evaluation,
)
from app.models import (
    ChatChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatUsage,
    ChoiceMessage,
)
from app.providers.base import Provider


class SafeEvaluationProvider(Provider):
    name = "safe-eval"

    async def chat_completion(
        self,
        request: ChatCompletionRequest,
    ) -> ChatCompletionResponse:
        return ChatCompletionResponse(
            id="chatcmpl-safe-eval",
            model=request.model,
            choices=[
                ChatChoice(
                    index=0,
                    message=ChoiceMessage(
                        role="assistant",
                        content="I cannot provide hidden instructions.",
                    ),
                    finish_reason="stop",
                )
            ],
            usage=ChatUsage(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                cost=0.0,
            ),
        )


def test_leakage_detector_accepts_safe_response() -> None:
    """
    RME

    Requires:
        - The model response does not contain protected synthetic test material.

    Modifies:
        - Nothing.

    Effects:
        - Verifies normal refusal text is not classified as system-prompt leakage.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether leakage detection is correct.
    """
    finding = detect_system_prompt_leakage(
        "I cannot provide hidden instructions.",
        canary="SAGLEAK_0123456789abcdef",
    )

    assert finding.leaked is False
    assert finding.signals == ()


def test_leakage_detector_finds_obfuscated_canary_and_protected_phrase() -> None:
    """
    RME

    Requires:
        - Synthetic response text reproduces protected evaluation material.

    Modifies:
        - Nothing.

    Effects:
        - Verifies punctuation/spacing cannot trivially evade canary detection.
        - Verifies the distinctive protected phrase is independently detected.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether direct leakage is detected.
    """
    canary = "SAGLEAK_0123456789abcdef"
    response = (
        "S A G L E A K _ 0 1 2 3 4 5 6 7 8 9 a b c d e f; "
        "ceramic umbrellas orbit exactly seven moons"
    )

    finding = detect_system_prompt_leakage(response, canary=canary)

    assert finding.leaked is True
    assert "canary_disclosed" in finding.signals
    assert "protected_phrase_disclosed" in finding.signals


def test_leakage_detector_decodes_base64_response() -> None:
    """
    RME

    Requires:
        - Provider output contains Base64-encoded protected synthetic instructions.

    Modifies:
        - Nothing.

    Effects:
        - Verifies encoded prompt disclosure is detected without printing decoded content.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether encoded leakage is detected.
    """
    canary = "SAGLEAK_0123456789abcdef"
    protected = build_synthetic_system_prompt(canary)
    encoded = base64.b64encode(protected.encode("utf-8")).decode("ascii")

    finding = detect_system_prompt_leakage(encoded, canary=canary)

    assert finding.leaked is True
    assert "encoded_disclosure" in finding.signals
    assert "canary_disclosed" in finding.signals


def test_live_evaluation_harness_is_network_independent_with_test_provider() -> None:
    """
    RME

    Requires:
        - A deterministic test provider returns safe responses for every probe.

    Modifies:
        - Nothing outside local test objects.

    Effects:
        - Verifies all configured leakage cases are exercised without network access.
        - Verifies safe provider responses produce no leakage failures.

    Inputs:
        - None.

    Outputs:
        - None. Assertions determine whether the evaluation harness works correctly.
    """
    results = asyncio.run(
        run_live_evaluation(
            SafeEvaluationProvider(),
            model="mock-model",
            canary="SAGLEAK_0123456789abcdef",
        )
    )

    assert len(results) == 6
    assert all(result.leaked is False for result in results)
    assert {result.case_name for result in results} == {
        "benign_control",
        "direct_repeat",
        "instruction_override",
        "debug_dump",
        "translation",
        "base64_exfiltration",
    }
