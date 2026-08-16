# Security and data handling

SignalRoute processes email content that may contain personal or commercially sensitive information. Treat source content as confidential production data.

## Implemented controls

- New Gmail connections request `gmail.readonly`; the engine calls only read endpoints.
- Existing OAuth tokens preserve their original grant so refresh remains compatible and emit a warning when broader legacy scopes should be replaced.
- OAuth credentials, tokens, `.env`, databases, and private data are ignored by Git.
- Refreshed Gmail tokens are written with mode `0600`.
- Model requests use `store=False` and strict response schemas.
- Source text is explicitly delimited as untrusted data in model instructions.
- Retrieval accepts only HTTP(S), checks DNS and every redirect, blocks non-public IP ranges and credentialed URLs, and limits crawl depth, page count, file size, and total bytes.
- ZIP members are selected by supported document type and bounded by member and aggregate size; paths from archives are never used as local destination paths.
- User-supplied bid-pack files are size-limited, restricted to supported document extensions, sanitised before local persistence, and subjected to the same safe ZIP extraction rules.
- Retrieved tender files and databases remain under the ignored private-data directory.
- The drafting prompt treats retrieved pages and files as untrusted evidence and prohibits source content from changing system policy or causing external actions.
- Generated outreach cannot be sent; approval only updates local review state.
- Logs use source IDs and operational state rather than email bodies.
- The review console binds to loopback by default.

## Hosted deployment requirements

The included review server is for local demonstration and trusted internal use. Do not expose it directly to the internet. A hosted deployment needs:

- authenticated users and role-based authorization;
- TLS and secure session handling;
- managed Postgres with encryption and backups;
- secrets stored outside the filesystem;
- tenant isolation if more than one organization is served;
- explicit retention and deletion policies for source bodies;
- audit-log access controls and export monitoring;
- rate limits and CSRF protection for state-changing endpoints;
- dependency and container scanning;
- provider agreements appropriate to the processed data.
- malware scanning and content-disarm controls before making downloaded files available outside the retrieval worker;
- an explicit per-domain crawling policy, robots/terms review, rate limiting, and authenticated-portal consent controls;

If credentials have ever been committed or shared, deleting the file is insufficient. Revoke the Gmail grant, rotate the OAuth client secret where applicable, and rotate the OpenAI API key.
