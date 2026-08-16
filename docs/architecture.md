# Architecture

## Design goals

SignalRoute is designed around five properties:

1. **Source independence** — Gmail is one connector, not the application boundary.
2. **Domain independence** — procurement behavior lives in a versioned playbook.
3. **Explainability** — facts carry evidence and decisions carry reasons.
4. **Operational safety** — inputs are idempotent, failures are isolated, and drafts require approval.
5. **Replaceable AI** — providers implement extraction and drafting contracts; business state does not depend on an SDK response type.

## Domain model

| Entity | Responsibility |
|---|---|
| `Signal` | Immutable inbound text plus source identity and metadata |
| `Opportunity` | Normalized facts and field-level source evidence |
| `Decision` | Playbook action, score, reason, target, and policy version |
| `Draft` | Proposed channel content and human-review state |
| `TenderDocument` | Retrieved page/file provenance, local content, status, and hash |
| `ProcessingRun` | Provider/model execution status and failure information |
| `Event` | Append-only audit history |

The model intentionally avoids procurement-specific columns. Vertical fields such as `notice_type_code` are stored as opportunity attributes and interpreted by the procurement playbook.

## Processing guarantees

The database owns the uniqueness constraint on `(source, external_id)`. This prevents two workers from creating duplicate signals. Successfully processed duplicates return the existing result. Failed signals can be retried; opportunity, decision, and draft records are updated through unique signal/opportunity relationships.

Each processing stage writes an audit event. A production queue can therefore use the signal status as a state machine:

```text
received → processing → processed
                 └───→ failed → processing
```

No delivery adapter exists in the current scope. Approval changes review state but cannot send a message, making accidental external side effects impossible.

## Retrieval and drafting boundary

Qualification runs before retrieval so ignored and monitoring-only signals never incur crawling or expensive document analysis. Actionable procurement opportunities enter a bounded retrieval stage:

```text
email URLs + extracted notice URL + exact-title web discovery
                         │
                         ▼
      validated HTTP fetch + relevant link traversal
                         │
                         ▼
 HTML / PDF / Office / spreadsheet / ZIP candidates
                         │
                         ▼
              Confidence-gated bid-pack classifier
                         │
                         ▼
        Governing ITT / specification / response / pricing pack
                         │
                         ▼
        structured bid working document + gaps
```

The application, not the language model, performs downloads and persists provenance. The drafting provider receives retrieved page text plus supported original files. This keeps network policy deterministic while allowing the model to reason over document layout, requirements, and response templates.

Web discovery is an optional seed generator, not the crawler. Portal-specific API or authenticated-browser adapters can be added behind the same retrieval contract without changing orchestration or drafting.

## Deployment evolution

The modular monolith should remain intact until scale proves a boundary needs extraction.

| Local implementation | Hosted implementation |
|---|---|
| SQLite repository | Postgres implementation of the same repository contract |
| Synchronous CLI loop | Durable queue worker with the same engine |
| Local review server | Authenticated ASGI application behind TLS |
| Process logs | Structured logs, traces, metrics, and alerting |
| Local OAuth token | Encrypted secret manager / delegated account connection |

This evolution changes infrastructure adapters rather than qualification policy or domain behavior.

## Evaluation strategy

Rules and model prompts should be evaluated separately:

- extraction: exact field accuracy, evidence validity, unsupported-claim rate;
- routing: action accuracy, high-risk false positives, explanation consistency;
- drafting: factual grounding, tone, correct target, and prohibited claims;
- retrieval: relevant-document recall, irrelevant crawl rate, access-wall detection, provenance, and source-pack completeness;
- operations: duplicate suppression, retry recovery, latency, and cost.

The included synthetic fixtures are smoke tests. A real deployment should maintain a redacted, labeled evaluation set and block prompt/playbook releases that regress agreed thresholds.
