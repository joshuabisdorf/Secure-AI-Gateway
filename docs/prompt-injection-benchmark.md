# Prompt-injection benchmark

Secure AI Gateway includes a deterministic, offline benchmark for the rule-based prompt-injection detector. The benchmark is a regression/evaluation tool; it does not call an LLM provider and does not measure whether an upstream model actually follows an attack.

## Run the benchmark

```bash
python -m app.evals.prompt_injection_benchmark
```

To make the committed baseline thresholds enforceable in automation:

```bash
python -m app.evals.prompt_injection_benchmark --enforce-baseline
```

To show only false-positive and false-negative case IDs, without printing prompt bodies:

```bash
python -m app.evals.prompt_injection_benchmark --show-errors
```

Machine-readable output is available for future CI/dashboard work:

```bash
python -m app.evals.prompt_injection_benchmark --format json
```

The evaluator prints the SHA-256 digest of the dataset so a result can be tied to the exact benchmark bytes that produced it.

## Version-1 dataset

The committed dataset is:

```text
evals/datasets/prompt_injection_v1.json
```

It contains 66 cases. Categories intentionally include both attack patterns the current detector handles well and patterns it does not yet handle well:

| Category | Label | Cases | Purpose |
| --- | --- | ---: | --- |
| `benign_general` | benign | 13 | ordinary non-attack traffic |
| `benign_security_hard_negative` | benign | 7 | legitimate security discussion containing attack-like vocabulary or quoted strings |
| `instruction_override` | attack | 6 | direct attempts to replace prior instructions |
| `system_prompt_extraction` | attack | 6 | attempts to disclose hidden/system/developer instructions |
| `role_impersonation` | attack | 5 | developer/admin/root/unrestricted role-switch attempts |
| `safety_bypass` | attack | 5 | explicit requests to disable or bypass controls |
| `secret_exfiltration` | attack | 5 | attempts to expose API keys, passwords, credentials, or secrets |
| `encoded_payload` | attack | 5 | Base64/hex payloads that decode to direct attack indicators |
| `typoglycemia` | attack | 5 | intentionally misspelled/scrambled attack wording |
| `indirect_injection` | attack | 5 | instructions presented as webpage/email/tool/document content |
| `multi_turn_split` | attack | 4 | attacks whose meaning is split across multiple messages |

These categories reflect attack classes called out in current OWASP prompt-injection guidance, including direct and indirect injection, encoding/obfuscation, typoglycemia, multi-turn attacks, system-prompt extraction, and data exfiltration.

## Metrics

A case is predicted `attack` when `inspect_prompt_injection()` returns one or more indicators. The evaluator computes:

```text
precision = TP / (TP + FP)
recall = TP / (TP + FN)
false_positive_rate = FP / (FP + TN)
false_negative_rate = FN / (TP + FN)
```

Per-category output reports the fraction of cases in that category detected by the current detector. For an attack category this is detection/recall for that category. For a benign category it is the category-specific false-positive rate.

The version-1 regression thresholds are:

```text
minimum precision:              0.80
minimum recall:                 0.75
maximum benchmark FPR:          0.40
```

These are baseline regression thresholds, not production acceptance criteria.

At the time v1 was introduced, the existing deterministic detector produced the following expected baseline shape:

```text
TP=36  FP=7  TN=13  FN=10
precision=0.8372
recall=0.7826
false_positive_rate=0.3500
false_negative_rate=0.2174
```

The weaknesses are intentional and visible. Direct and encoded cases are detected well, while typoglycemia and split multi-turn attacks are missed. The hard-negative security-discussion set also exposes the current regex layer's inability to reliably distinguish quoted/descriptive attack text from an instruction directed at the model.

## Interpreting the false-positive rate

The reported FPR is the rate on this curated benchmark only. It is **not** an estimate of false positives in real production traffic. The benign set deliberately over-represents difficult security-domain examples so regressions and over-broad rules are easier to see.

A production FPR estimate requires a representative, independently sampled benign traffic corpus with appropriate privacy controls and labeling methodology.

## Versioning policy

Once a benchmark version is established, do not silently rewrite its cases or labels to improve detector scores. Material dataset changes should create a new file, for example:

```text
prompt_injection_v2.json
```

Detector improvements may raise v1 scores; tests should not require the original exact confusion matrix. The committed baseline thresholds are floors/ceilings intended to prevent regressions while allowing improvement.

A future benchmark version should add broader indirect-injection sources, Unicode/spacing obfuscation, larger multi-turn sets, RAG/tool-output cases, and independently sourced adversarial examples.

## Security and privacy boundary

The dataset contains only synthetic test prompts. The evaluator does not require API keys, does not call providers, and does not print prompt bodies in its result report. `--show-errors` prints case IDs only.
