# Promotion policy

## Purpose

`PromotionPolicy` is a deterministic, side-effect-free evaluator over an observation and its evidence summary. It produces a calibration recommendation only.

## Flow

```text
validated Observation + Redis EvidenceSummary -> PromotionDecision
```

The decision includes eligibility, `PROMOTE` or `COLLECT_MORE_EVIDENCE`, reason, score, threshold, evidence summary, and evaluation timestamp.

## Inputs and decisions

The current calculation considers observation count, distinct sessions, source
count, confidence, source quality, provenance, and recency. Enterprise evidence
is treated as authoritative when source type and a stable source-record key establish it.
Canonical writers pass a fact key so the reader uses canonical evidence.

## Current limitation

The policy is shadow-only. Existing writers still determine persistence even
when the recommendation is `COLLECT_MORE_EVIDENCE`. The policy never writes,
queues work, changes profile versions, or changes routing.

## Future work

Comparison data is being collected to determine whether enforcement is justified. No enforcement cutover is implemented.
