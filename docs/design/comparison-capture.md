# Promotion comparison capture

## Purpose

Promotion comparison capture records the difference between a shadow policy recommendation and the outcome of the existing writer. It supports calibration without changing writer control flow.

## Flow

```text
writer completes -> PROMOTED / NOT_PROMOTED / WRITER_FAILED
                 -> PromotionComparisonRecorder -> redacted JSONL in logs/
```

Canonical and legacy durable writes map to `PROMOTED`. Profile and enterprise
cache writers use their existing `changed` result. Exceptions are recorded as
`WRITER_FAILED` and re-raised unchanged.

## Records and interactions

Records include opaque observation identifiers, writer outcome, decision,
scores, evidence/session/source counts, confidence, provenance presence, and
enterprise-authority status. They do not include raw user content or workbook values.
Files are role- and date-specific under `logs/`.

`scripts/promotion_calibration_report.py` reads records, deduplicates comparison keys, and reports agreement, disagreement, false promotions, missed promotions, and factor distributions.

## Limitations

The recorder is best effort and never blocks a writer. It measures agreement with current behavior; it is not a promotion gate.
