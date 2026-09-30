# Processing pipeline

## Purpose

This page describes the implemented request, observation, and background-work paths.

## Request path

```text
POST /query
  -> authenticate principal
  -> archive user message and update Redis conversation window
  -> deterministic router
  -> retrieve profile, conversation, memory, and authorized Excel data
  -> assemble ContextPackage
  -> build bounded prompt
  -> Gemini answer call, with Groq fallback
  -> archive assistant message
```

`ContextOrchestrator` does not mutate durable state while retrieving context.
It can return internal `ContextWriteIntent` objects for cache or refresh work.
After assembly, the API atomically queues each distinct intent to Redis Streams;
the worker dispatches it to the existing writer. Context retrieval itself never
enqueues or applies the intent.

## Observation and write path

```text
Ingress -> Observation -> ObservationValidator -> EvidenceStore
        -> PromotionPolicy.evaluate() [shadow only] -> existing writer
        -> Promotion Comparison Capture -> Redis vector-projection job -> ChromaDB
```

The worker handles `memory-extraction`, `context-write-intent`,
`profile-observation`, `enterprise-cache`, and `vector-projection` streams. Redis Streams provide
bounded retries, dead-letter streams, and consumer-group recovery.

## Conversation extraction

```text
Evicted user message -> memory-extraction job -> Groq JSON extraction
                     -> ExtractedFact -> FactNormalizer
                     -> canonical fact writer or PROFILE_NOTE handling
```

Supported predicates are normalized through the predicate registry.
Unsupported safe self-referential statements can become `PROFILE_NOTE`.
Questions and general knowledge are ignored.

## Limitations

- Extraction uses an LLM and can return no facts or unsupported predicates.
- The default worker process hosts multiple queue consumers.
- Duplicate deferred intents are coalesced atomically before they enter the worker queue.

## Related documents

- [Observation model](../design/observation-model.md)
- [Evidence store](../design/evidence-store.md)
- [Extraction trace mode](../extraction_trace.md)
