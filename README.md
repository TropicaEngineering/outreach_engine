# SignalRoute

SignalRoute turns inbound public-sector tender alerts into qualified opportunities, clean bid-pack handoffs, and evidence-grounded first working drafts.

## What it demonstrates

SignalRoute is a human-in-the-loop tender automation pipeline:

```text
Gmail alert
  → deterministic notice identification and deduplication
  → official Find a Tender record and application route
  → governing bid-pack documents
  → structured requirements and submission map
  → tailored first-pass response
  → editable Word response + separate Excel pricing schedule
  → human review and portal submission
```

The important design choice is the boundary between deterministic automation and AI.
Notice IDs, duplicates, workflow state, document provenance, file packaging and review
status are handled by ordinary application code and SQLite. Models are used where the
input is genuinely unstructured: extracting opportunity facts, interpreting inconsistent
tender documents and drafting buyer-specific answers. Every generated result remains
reviewable and nothing is submitted automatically.

## Architecture at a glance

| Layer | Responsibility |
| --- | --- |
| Connectors | Pull Gmail alerts, fixtures or legacy local messages into one `Signal` contract. |
| Extraction | Convert unstructured alerts into a sourced `Opportunity`. |
| Playbook | Apply procurement-stage rules and decide whether the opportunity needs review. |
| Route resolution | Use the exact notice ID and public OCDS record to identify the advert, portal, download or explicit email route. |
| Bid-pack retrieval | Store and classify governing documents conservatively; user upload handles portal boundaries. |
| Drafting | Read the verified pack, map submission requirements and create structured bidder-facing content. |
| Artifact export | Turn the submission map into editable Word, Excel and ZIP files while preserving manual-form and portal actions. |
| Persistence | Keep workflow state, provenance, events, bid profile and drafts in local SQLite. |
| Review console | Provide the three-step browser workflow and require human approval. |

The engine depends on interfaces for connectors, extractors, drafters and retrievers.
That keeps Gmail, OpenAI, web retrieval and procurement-specific decision rules modular
instead of embedding them in the UI or orchestration code.

For a maintainer-oriented walkthrough, see
[`docs/ARCHITECTURE_BRIEF.md`](docs/ARCHITECTURE_BRIEF.md).

It uses saved-search email as the discovery trigger, enriches each exact Find a Tender notice through the public OCDS record, and presents the explicit document route from that notice. The user can then drop in the bid pack and create an evidence-grounded working document for human approval.

## Why this exists

Teams receive useful intent signals through email alerts, webhooks, spreadsheets, marketplaces, and CRMs. The difficult part is not collecting them; it is consistently deciding:

- Is this relevant?
- What evidence supports that conclusion?
- Should we bid, engage, partner, monitor, or ignore?
- Who is the right contact?
- What should the first message say?

SignalRoute makes that workflow explicit and auditable.

## System shape

```text
Gmail / fixture / future API
            │
            ▼
        Connector
            │
            ▼
    Immutable Signal ───────────────┐
            │                       │
            ▼                       │
  Evidence-based Extraction        │ audit events
            │                       │
            ▼                       │
 Normalized Opportunity            │
            │                       │
            ▼                       │
  Versioned Playbook                │
            │                       │
            ▼                       │
 Exact Notice Route                │
            │                       │
            ▼                       │
     Bid-pack Upload ─────────────► Evidence-grounded Draft ──┘
                                            │
                                            ▼
                                       Human Review
```

The application is a modular monolith. SQLite provides a zero-infrastructure local deployment; its repository boundary is the seam for Postgres in a hosted environment. Live providers are optional and lazily imported, so the complete offline path runs without credentials or third-party packages.

## Run the offline demo

Python 3.11 or newer is the only requirement.

```bash
make demo
make test
make serve-demo
```

Then open [http://127.0.0.1:8080](http://127.0.0.1:8080). The included fixtures are synthetic and safe to show in a screen share.

The same workflow without `make`:

```bash
PYTHONPATH=src python3 -m outreach_engine.cli demo --reset
PYTHONPATH=src python3 -m outreach_engine.cli --database data/demo.sqlite3 serve
```

To run the same engine against generic partnership and inbound-sales signals:

```bash
make demo-universal
make serve-demo
```

## Run the configured live pipeline

With `.env`, `.env.local`, and the ignored Gmail credential files configured:

```bash
make live
make serve
```

`make live` ingests filtered Gmail alerts, performs stable notice-level deduplication and structured extraction, then resolves the exact notice through the public Find a Tender OCDS endpoint. `make serve` opens the configured live database in the review console. The user follows the official download, portal, or explicit email route, uploads the resulting pack, and selects **Generate bid workspace**.

This staged default keeps inbox maintenance cheap and predictable. Route enrichment never performs broad web research: it reads the exact notice ID supplied by the alert and persists its result. Drafting failures preserve the uploaded bid pack.

## Deterministic bid-pack handoff

Find a Tender saved-search email provides discovery. SignalRoute extracts the notice ID and requests only that notice's public OCDS release package. It reads `tender.submissionMethod`, `tender.submissionMethodDetails`, `tender.documents`, and the official deadline, then displays one clean outcome:

- direct bidding-document download;
- named external procurement portal;
- explicit email route stated in the notice; or
- the original advert when no separate route is stated.

Generic buyer contact addresses are never treated as submission addresses unless the tender instructions explicitly say so. The public API response and resolved handoff are cached with the opportunity. Broad crawling remains an optional diagnostic capability, not part of the main review workflow.

## Confidence-gated bid-pack retrieval

For actionable procurement signals, retrieval starts with the extracted opportunity URL (falling back to links in the signal only when no primary URL was extracted). Authoritative notice data and its embedded portal links are preferred. Broader web discovery is skipped when an official Find a Tender data record is available.

Downloaded files are classified conservatively as tender instructions, requirements/specification, response template, pricing schedule, evaluation criteria, or terms. A document only reaches the review UI and drafting model when it has a high-confidence governing role. Topic-adjacent policy pages, generic search results, privacy pages, and unsupported material are withheld.

The user sees one of three terminal outcomes: **bid pack verified**, **official portal handoff**, or **no verified pack found**. These results are persisted instead of being rerun during the normal review flow. When automated retrieval stops at a portal boundary, SignalRoute opens only the confirmed official portal and accepts user-downloaded PDF, Word, Excel, PowerPoint, text, or ZIP files. An uploaded governing ITT or specification unlocks drafting; notice metadata alone does not.

Find a Tender saved-search digests are split into one signal per notice and deduplicated by notice ID. This prevents a multi-notice email—or the same notice appearing in repeated alert emails—from producing unstable or duplicate opportunities.

The crawl is deliberately bounded by page count, depth, per-resource size, and total size. It validates every redirect and blocks credentials in URLs, non-public IP addresses, localhost, link-local targets, and non-standard ports. Each retrieved source keeps its original URL, final URL, filename, media type, size, content hash, and crawl depth.

Live tenders routed to `review` produce an editable response pack rather than a long
paraphrase of the tender instructions. The drafting stage:

- identifies every requested submission deliverable and drafts it in the buyer's order;
- follows supplied headings, questions, word limits and response templates;
- builds method statements, quality answers, implementation plans, forms and cover text;
- reproduces required pricing-schedule line items and, only against an explicit maximum
  budget, targets the configured percentage below that ceiling;
- separates genuinely missing company evidence and portal checks from completed work;
- produces a concise bid brief, submission checklist and source manifest.

The **Bid profile** in the review console stores the reusable writing tone, company
overview, relevant experience, proof points, pricing target and commercial assumptions.
It is stored in the local SQLite database and supplied to the drafting model on each
generation. Blank profile fields become explicit company-input markers; they are never
filled with invented claims. Generated response packs can be regenerated after a profile
change and exported as a clean set of editable submission files.

The response workspace deliberately separates internal bid controls from bidder-facing
content. The brief, prepared-document status, submission checklist, portal actions and
missing company inputs appear in a control panel above the response. The editable export
uses that submission map to create a polished Word response, a separate formula-driven
pricing workbook whenever pricing is required, and separate Word attachments only where
the buyer explicitly requests and enough content exists. The whole set is also available
as one ZIP. Buyer forms and portal fields are never falsely represented as completed;
they remain exact named actions in the review workspace. Confirmed portal and submission
routes remain clickable throughout the workflow.

Uploaded pack files can be removed with a two-step confirmation. Removing a source file
immediately rechecks the remaining pack and clears any generated response that may have
used that file, preventing a stale bid from being approved accidentally. Duplicate copies
of the same source content are collapsed before drafting.

Tender-specific gaps are completed inline rather than through another general-purpose
prompt. Each missing name, project example, fee, rate or reference becomes a labelled
field in the response control panel. **Save inputs & regenerate** persists those facts on
that opportunity and immediately rebuilds the response. Resolved inputs are incorporated
into the bid and disappear from the remaining-input list. Prepared-document and submission
checklists stay collapsed until the reviewer wants their supporting detail.

Each generated response also includes a compact assurance layer. It names the governing
documents and instruction areas checked, surfaces buyer-specific win themes carried into
the writing, and maps every submission item to one of five handling routes: generated in
the pack, complete manually, attach separately, enter in the portal, or commercial check.
This makes mixed council instructions explicit without pretending that a portal form has
been completed automatically. Pricing, later-phase fees and day rates remain visible as a
non-blocking commercial review rather than repeated regeneration inputs.

Pre-market engagement and awarded notices retain stage-appropriate email drafting. Login walls and inaccessible documents are shown as retrieval issues; they are never treated as successfully collected evidence.

## CLI

```text
signal-route init-db
signal-route demo --reset
signal-route ingest-fixtures fixtures/procurement_signals.json
signal-route ingest-directory ./raw_emails --metadata-dir ./parsed
signal-route ingest-fixtures leads.json --playbook universal
signal-route ingest-gmail --provider openai --limit 25
signal-route ingest-gmail --provider openai --retrieve --limit 25
signal-route ingest-gmail --provider openai --retrieve --limit 25 --notice-id 076578-2026
signal-route list
signal-route show <opportunity-id>
signal-route review <draft-id> approved
signal-route serve
```

Every signal is identified by `(source, external_id)`. Replaying a successfully processed input is a no-op; failed inputs can be retried without creating duplicate opportunities.
Use `ingest-directory --force` after a playbook change to re-evaluate existing records in place.

## Live Gmail and OpenAI mode

Create an isolated environment and install the optional adapters:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[all]'
cp .env.example .env
```

Add `OPENAI_API_KEY` to `.env`, then place Gmail desktop OAuth credentials at the configured `GMAIL_CREDENTIALS_PATH`. SignalRoute requests the read-only Gmail scope and writes the resulting local token with owner-only permissions.

```bash
signal-route ingest-gmail --provider openai --playbook procurement
```

The AI adapter uses the OpenAI Responses API with strict JSON Schema outputs, bounded retries, request timeouts, `store=False`, prompt-injection boundaries, and evidence captured as exact source excerpts. Retrieved files are supplied as per-request file inputs to the drafting model; downloaded originals remain local under the ignored document path. Extraction and drafting models can be configured separately with `OPENAI_EXTRACTION_MODEL` and `OPENAI_DRAFTING_MODEL`.

## Playbooks make it universal

A playbook owns scoring and routing, not extraction or ingestion. The included playbooks are:

- `procurement`: understands UK1/2/3/4/6/7 stages and routes to bid review, buyer engagement, delivery partnership, monitoring, or ignore.
- `recruitment`: qualifies candidate and vacancy signals using the same engine and review flow.
- `universal`: qualifies generic commercial intent from explicit budget, deadline, partnership, implementation, software, and integration signals.

Adding an inbound-sales, recruitment, partnership, or support playbook requires implementing one `decide(opportunity)` contract. Connectors and persistence do not change.

## Reliability and safety decisions

- Source records are immutable after ingestion.
- Database uniqueness enforces idempotency.
- Processing runs capture provider, model, playbook, status, and errors.
- Each input fails independently; one malformed message does not stop the batch.
- Partial runs are retryable and update the existing opportunity.
- Decisions include score, reason, target, playbook, and playbook version.
- Generated messages remain pending until a human approves or rejects them.
- Original text and field-level evidence stay visible beside each decision.
- Web retrieval is bounded and blocks private-network destinations to reduce SSRF risk.
- Retrieved files remain linked to their source URL and content hash.
- Missing or inaccessible tender material is surfaced instead of silently ignored.
- Secrets, OAuth tokens, local databases, caches, and private data are excluded from Git.

See [Architecture](docs/architecture.md) and [Security](docs/security.md) for boundaries and production deployment notes.

## Repository map

```text
src/outreach_engine/
├── connectors/       # Gmail, fixtures, and future source adapters
├── playbooks/        # Versioned qualification and routing policy
├── providers/        # Deterministic and OpenAI extraction/drafting
├── web_assets/       # Local human-review console
├── domain.py         # Typed domain entities and lifecycle states
├── engine.py         # Idempotent orchestration
├── storage.py        # SQLite schema, persistence, and audit log
├── web.py            # Local JSON API and review server
└── cli.py            # Operational entry point
```

## Deliberate scope

This repository demonstrates the complete product workflow without pretending a local prototype is already a hosted multi-tenant service. Before public deployment, replace the local web server with an authenticated application server, move persistence to managed Postgres, run ingestion in a durable worker, add centralized telemetry, and establish retention policies for source content.
