# Extraction trace mode

`EXTRACTION_TRACE=false` is the default and leaves the memory pipeline unchanged. Set
`EXTRACTION_TRACE=true` only in a local development environment. Trace records include
original user content and must not be enabled for production or sensitive data.

## Pipeline

Each extraction job has a `trace_id`. When the API request evicts a user message, its
request ID is propagated to the Redis extraction job, Neo4j fact write, and vector job.
The trace is written as JSON Lines to `logs/extraction/<trace_id>.jsonl`.

```text
Memory Worthiness
  -> Extraction
  -> Canonicalization
  -> Graph Write
  -> Validation
  -> Chroma Projection
```

The console emits one concise `extraction_trace trace_id=... stage=...` line for every
trace stage. Router JSON logs include the same ID while trace mode is enabled.

## Trace format

Every line is a standalone JSON object with `trace_id`, `timestamp`, and `stage`.
Important stages are:

- `pipeline_start`: original evicted user message and owner.
- `memory_worthiness`: `STORE` or `IGNORE`, with a reason.
- `extracted_fact`: extractor entities and subject/predicate/object output.
- `canonical_fact`: normalized graph identity and display text.
- `graph_write_details`: Cypher, MERGE targets, relationship types, and Neo4j counters.
- `final_stored_memory`: durable display text, owner, predicate, authority rank, and type.
- `validation` and `validation_warning`: non-blocking checks.
- `chroma_projection` and `chroma_projection_details`: asynchronous vector projection.

Example:

```json
{"trace_id":"a1...","stage":"memory_worthiness","decision":"STORE","reason":"supported_canonical_predicate"}
{"trace_id":"a1...","stage":"canonical_fact","subject_key":"person:E0001","predicate":"REPORTS_TO","object_key":"person:E0001:gyan-gupta"}
{"trace_id":"a1...","stage":"validation_warning","severity":"warning","code":"reports_to_both_directions","message":"REPORTS_TO exists in both directions"}
```

## Debugging workflow

1. Enable `EXTRACTION_TRACE=true` and restart API and worker containers.
2. Send enough messages to evict the target user message from the active window.
3. Locate the trace file under `logs/extraction/` using the request ID from router logs.
4. Follow the stages in order. The first stage where data becomes wrong identifies the
   responsible boundary: extractor output, canonicalization, graph persistence, or projection.

Common patterns:

- `IGNORE/extractor_returned_no_facts`: the LLM did not consider the message durable.
- Wrong `extracted_fact`: the issue begins in extraction.
- Correct extraction but wrong `canonical_fact`: normalization introduced the issue.
- Correct canonical fact but unexpected `graph_write_details`: persistence/query issue.
- Correct graph write but wrong answer later: inspect the `chroma_projection` event separately.

## Replay

Replay a saved conversation directly through the worker path:

```powershell
docker compose exec worker python scripts/replay_extraction.py /app/data/conversation.json
```

The input is either a JSON object containing `user_id`, `agent_id`, and `messages`, or a
JSON messages array. It uses the normal extractor, normalizer, graph writer, and vector
queue; it does not require the dashboard or API. It deliberately writes a new trace ID
unless the input supplies one.
