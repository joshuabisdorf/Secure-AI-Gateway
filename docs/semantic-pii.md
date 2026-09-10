# Semantic PII detection and evaluation

Secure AI Gateway combines two PII layers before provider forwarding:

1. deterministic structured recognition for email addresses, U.S. SSNs, common North American phone numbers, and Luhn-valid payment-card numbers;
2. local semantic/contextual recognition for person names, personal locations, dates of birth, and street addresses.

The structured layer runs first. Semantic analysis therefore sees text in which already-recognized structured values have been replaced with type-specific placeholders.

## Local NLP boundary

The semantic layer uses spaCy with the pinned `en_core_web_sm` 3.8.0 English pipeline. Model inference runs inside the gateway process and does not send message text to an external PII or LLM service.

The backend selector is deployment configuration:

```dotenv
SAG_SEMANTIC_PII_BACKEND=spacy
```

`spacy` is currently the only supported backend and is the default. Unsupported backends or an unavailable model fail closed with:

```text
HTTP/1.1 503 Service Unavailable
```

```json
{"detail":"PII detection is unavailable."}
```

There is no silent structured-only fallback when semantic inspection is expected.

## Contextual entities

The current semantic types are:

```text
person_name        -> [REDACTED_PERSON]
personal_location  -> [REDACTED_LOCATION]
date_of_birth      -> [REDACTED_DOB]
street_address     -> [REDACTED_ADDRESS]
```

Named-entity recognition alone is not sufficient to classify a value as personal. The detector applies bounded contextual rules around spaCy entities. Examples of personal context include `my name is`, `contact`, `patient`, `account holder`, `I live`, `resides in`, `date of birth`, `DOB`, `born on`, and `birthday`.

This reduces obvious over-redaction such as treating every mention of a public figure, city, or historical date as private data.

Street addresses use a complementary bounded structured recognizer because the small English NER model does not provide a dedicated postal-address entity. Over-redaction remains possible for public or example addresses and is measured explicitly in the benchmark.

## Request behavior

The existing profile setting still controls the complete PII stack:

```json
"pii_action": "redact"
```

or:

```json
"pii_action": "deny"
```

`redact` replaces both structured and semantic findings in the copied provider request. `deny` rejects when either layer finds PII. Raw matched values are not passed to audit logging.

The safe response/audit type metadata can now include:

```text
person_name
personal_location
date_of_birth
street_address
```

alongside the existing structured types.

## Offline benchmark

The version-1 dataset is:

```text
evals/datasets/semantic_pii_v1.json
```

Run it with:

```bash
python -m app.evals.semantic_pii_benchmark --show-errors
```

Enforce the committed regression thresholds with:

```bash
python -m app.evals.semantic_pii_benchmark \
  --enforce-baseline \
  --show-errors
```

The evaluator reports case-level TP/FP/TN/FN, precision, recall, false-positive rate, false-negative rate, per-category detection rates, dataset SHA-256, and optional error case IDs. It never prints benchmark text in normal or error output.

The benchmark includes personal-context positives and hard negatives covering public people, general locations, generic dates, and public/example/fictional addresses. Its false-positive rate is a regression metric on this curated set, not an estimate of real production traffic.

## Versioning

Meaningful changes to labels, cases, or benchmark composition should create `semantic_pii_v2.json` rather than silently rewriting v1. Detector improvements can then be compared against a stable historical baseline.

## Limitations

This is a first semantic layer, not comprehensive PII/PHI recognition. The small English NER model can miss names and locations, contextual rules can miss unusual phrasing, and the address recognizer can over-match public/example addresses. The current layer is English-focused and does not yet attempt broad medical/clinical entity detection, arbitrary account identifiers, relationship inference, or cross-message identity resolution.

Automated PII detection should be treated as a defense layer rather than a guarantee that all sensitive data has been identified.
