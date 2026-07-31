# Testing

## Purpose

The pytest suite protects API behavior, context retrieval, canonicalization, observation validation, evidence, shadow policy evaluation, and comparison capture.

## Run tests

```powershell
docker compose run --rm --no-deps --entrypoint python worker -m pytest -q
```

Run one module:

```powershell
docker compose run --rm --no-deps --entrypoint python worker -m pytest tests/test_evidence_collection.py -q
```

## Coverage and decisions

| Area | Current coverage |
|---|---|
| API and context | Demo-principal isolation, explicit memory retrieval, precedence, and pure reads. |
| Observations | Immutability, serialization, validation failures, and lineage. |
| Evidence and policy | Idempotency, canonical aggregation, replay safety, and deterministic shadow decisions. |
| Writers and comparison | Existing write order, failure preservation, redacted output, and retry deduplication. |
| Canonical facts | Predicate normalization, scoped entities, and worker output. |

Tests use lightweight external-store fakes where live services are unnecessary. Shadow tests assert evaluation does not mutate evidence or alter current writer outcomes.

## Limitations

Tests do not replace manual validation against sanitized workbooks, real providers, or running-container performance behavior.
