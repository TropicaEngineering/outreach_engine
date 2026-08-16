# SignalRoute architecture brief

> Personal maintainer note. This explains the system in plain English and is not intended
> as interview marketing copy.

## The idea in one sentence

SignalRoute turns an inbound tender notification into the actual editable submission
working files while keeping a person responsible for evidence, commercial approval and
the final portal submission.

The product is not “an agent that bids for you.” It is a controlled pipeline that uses an
LLM for the two genuinely fuzzy jobs: understanding messy text and writing a tailored
first pass.

## The end-to-end path

1. **Ingest:** Gmail supplies Find a Tender saved-search emails. Other connectors can feed
   the same engine for fixtures, old local messages or future opportunity sources.
2. **Parse:** the extraction model turns the email into the shared `Opportunity` schema and
   stores evidence for the extracted fields.
3. **Qualify:** the procurement playbook uses ordinary rules to decide whether the notice
   is a live tender worth reviewing, pre-market engagement, an award, or noise.
4. **Resolve:** when a Find a Tender notice ID exists, application code requests that exact
   public OCDS release package. It extracts the deadline, official advert and the explicit
   download, portal or email route. This is deterministic and cached.
5. **Get the pack:** openly available governing documents can be retrieved and classified.
   If a portal requires a login, the UI takes the user to the confirmed portal and accepts
   the downloaded pack by drag and drop.
6. **Draft:** the stronger model receives only the verified core documents, reusable bid
   profile and opportunity-specific facts. It returns a strict structured response: brief,
   deliverables, pricing schedule, submission checklist and genuine missing inputs.
7. **Package:** normal code converts that structure into the requested file set. Narratives
   become editable Word content; pricing becomes a formula-driven Excel workbook; clearly
   separate drafted attachments get their own Word files; everything is also zipped.
8. **Review:** the human sees requirements coverage, win themes, files produced, portal
   actions and missing evidence before approving or submitting anything.

## Where the two model stages sit

### Stage 1: extraction

Purpose: turn a messy notification into reliable routing data.

Input: email subject/body and source metadata.

Output: opportunity title, buyer, summary, value, deadline, location, URL, notice type and
field-level evidence.

This stage should remain relatively cheap. It is not responsible for writing a bid or
browsing randomly around the web.

### Stage 2: bid drafting

Purpose: interpret the verified bid pack and prepare a usable first-pass submission.

Input: opportunity, procurement decision, core tender files, local bid profile and saved
opportunity-specific evidence.

Output: strict JSON matching `BID_DRAFT_SCHEMA`, followed by a bidder-facing Markdown
preview and editable artifacts.

This is the expensive stage because it must reconcile scattered requirements, follow the
buyer’s order, identify tailored win themes, preserve missing-evidence markers and draft
substantive answers rather than paraphrasing the instructions.

## What is deterministic

- uniqueness of `(source, external_id)` and Find a Tender notice deduplication;
- SQLite workflow and audit state;
- exact notice API lookup and route extraction;
- bounded retrieval, redirect validation and private-network blocking;
- document provenance, hashes and conservative core-document classification;
- draft gating: a verified governing pack is required;
- pricing arithmetic against an explicit maximum budget;
- submission-file planning, filenames, Word/Excel creation and ZIP packaging;
- review status and the rule that nothing is submitted automatically.

This matters because the interview story is not “the model usually works.” The story is
“the model is contained inside an auditable workflow with deterministic boundaries.”

## What the model decides

- how unstructured language maps into the opportunity schema;
- which requirements and outputs the governing documents actually request;
- the buyer-specific response structure and first-pass prose;
- which supplied company strengths are relevant win themes;
- whether an item is generated content, a separate attachment, a buyer form, a portal
  entry or a commercial check;
- which non-commercial facts are genuinely missing.

Those decisions are constrained by strict schemas and source material. The model is told
not to invent people, projects, accreditations or compliance claims.

## Code map

```text
src/outreach_engine/
├── cli.py                    composition root and CLI commands
├── config.py                 .env loading and runtime settings
├── domain.py                 shared dataclasses and enums
├── engine.py                 workflow orchestration
├── storage.py                SQLite schema, persistence and audit events
├── bid_profile.py            reusable supplier-profile defaults/validation
├── web.py                    local JSON API and static review console
├── artifacts.py              deterministic submission-file planning/export service
├── connectors/               Gmail, fixture and directory adapters
├── playbooks/                procurement/recruitment/universal decision rules
├── providers/                heuristic and OpenAI extraction/drafting implementations
├── retrieval/                safe web access, route resolution and pack classification
└── web_assets/               cream browser UI

scripts/
└── build_submission_pack.mjs Word and formula-driven Excel generation
```

The central object is `OutreachEngine`. It does not know how Gmail, OpenAI or a particular
web retriever works; it coordinates their contracts and persists each transition.

## Storage model

The local SQLite database holds:

- `signals`: immutable inbound records and processing state;
- `opportunities`: structured opportunity data and workflow attributes;
- `decisions`: playbook result and reason;
- `tender_documents`: provenance, local path, hash, classification and extracted text;
- `drafts`: bidder-facing body plus structured drafting metadata;
- `events`: audit trail for important transitions;
- `processing_runs`: provider/model run status;
- `application_settings`: the reusable bid profile.

`data/private/` is intentionally ignored by Git. It contains the real database, downloaded
documents and generated submission files.

## Why email discovery is still useful

The Find a Tender API is useful once the system knows the exact notice ID, but it is not a
personalised saved-search inbox. Email supplies the user’s discovery/filtering layer; the
public API then supplies the authoritative machine-readable record. Using both gives a
cleaner MVP than pretending the API is a complete personalised feed.

## Honest product boundary

The system can reliably create a strong first pass and remove most context gathering,
instruction hunting, document setup and repetitive drafting. It cannot guarantee that an
unknown council template or authenticated portal has been completed correctly without
seeing and safely interpreting that exact interface.

Therefore:

- if content can be drafted, it is drafted;
- if a separate file is evidenced, it is packaged separately;
- if the council’s own form is required, the system names that action rather than faking it;
- if evidence such as a CV or verified project result is absent, it remains visibly absent;
- a human checks commercial figures and submits the final files.

That is the defensible “90% automation” claim.

## Interview demo script

1. Open the local workspace and say: “These are real saved-search tender alerts, already
   parsed and deduplicated.”
2. Open the Lymington opportunity and show the exact official advert and portal route.
3. Show the governing PDF in the bid-pack step: “The model cannot draft from notice noise;
   it needs the governing document.”
4. In the response step, point to requirements coverage and tailored win themes: “It has
   mapped the pack before writing.”
5. Show the submission handling counts: generated, separate attachment, portal entry and
   commercial check.
6. Download the editable pack. Open the Word response and separate pricing spreadsheet.
7. End with: “The automation removes the blank-page and document-assembly work, but it
   does not invent evidence or submit on someone’s behalf.”

## Running it tomorrow

```bash
cd "/Users/m4main/Documents/ChatGPT/Outreach engine refactor"
make serve
```

Then open `http://127.0.0.1:8766`.

If the port is already in use, the server is probably already running. Use the existing
browser tab or stop the old terminal process with `Ctrl+C` before starting another.

Useful checks:

```bash
make test
.venv/bin/signal-route list
.venv/bin/signal-route show <opportunity-id>
```

## Before sharing the repository

- never include `.env`, Gmail OAuth credentials/tokens, `data/private/` or real buyer files;
- rotate any credentials that previously lived in the crude original folder;
- use sanitized fixtures for screenshots or a public portfolio copy;
- decide whether this personal architecture brief should be excluded from the version you
  hand to an interviewer.
