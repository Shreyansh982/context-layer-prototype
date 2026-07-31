# Canonical facts

## Purpose

Canonical facts represent durable typed assertions independently of their original wording. They provide stable graph identity, owner-scoped entities, provenance observations, and semantic retrieval.

## Model

```text
(:Entity)-[:SUBJECT_OF]->(:Fact)<-[:SUPPORTS]-(:FactObservation)
(:Fact)-[:OBJECT_OF]->(:Entity or value)
(:Entity)-[:PREDICATE {fact_key}]->(:Entity or value)
```

`fact_key` hashes the canonical subject, predicate, object/value, and qualifiers. It is the graph identity and canonical evidence key.

## Responsibilities and interactions

`PredicateRegistry` resolves the closed in-code vocabulary and aliases.
`FactNormalizer` resolves first-person references and the authenticated employee ID to the owner profile, scopes other people to the owner's graph, and prevents self-relationship edges.
`CanonicalFactService.upsert()` merges entities, facts, relationships, and provenance, then queues Chroma projection.

## Important decisions

- Supported work and social predicates use entity relationships.
- Profile preferences, notes, and enterprise fields use registry-defined storage modes.
- Complete-profile requests retrieve all owner facts; other requests use semantic retrieval with graph fallback while projection is pending.

## Limitations

- The predicate vocabulary is closed and implemented in Python.
- Unsupported safe self statements may become `PROFILE_NOTE`; general and third-party unsupported content is ignored.
- Typed facts do not by themselves provide causal reasoning.
