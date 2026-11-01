# Security model

## Authentication

* Email + password, scrypt (n=16384, r=8, p=1) with a per-user random salt.
* JWT HS256 access tokens (`iss=archgraph`, expiry `ACCESS_TOKEN_EXPIRE_MINUTES`,
  default 12h). No refresh tokens; sign in again after expiry.
* API keys (`agk_…`) stored only as SHA-256 hashes with a public prefix for
  identification. Keys can be scoped to a single workspace and revoked at any time.
  Revocation is immediate because every request re-reads the key row.

## Authorization

Roles are ranked: `SYSTEM_ADMIN < WORKSPACE_ADMIN < ARCHITECT < DEVELOPER < VIEWER`
(tighter = lower index). Every workspace route calls `ensure_workspace_access`,
which checks membership (or ownership), the required role, and — crucially — the
API key's workspace scope *before* any admin bypass. System administrators do not
bypass an API key's scope; a scoped key is a hard boundary.

| Action | Minimum role |
| --- | --- |
| Read workspace, services, graph, docs | VIEWER (member) |
| Trigger scans, sync documents, upload docs | DEVELOPER |
| Add/remove repositories, confirm inferred edges, review AI | ARCHITECT |
| Workspace settings, members, webhook secrets, AI providers | WORKSPACE_ADMIN |
| Users, system info, global audit | SYSTEM_ADMIN |

## Secrets

* Integration credentials and repository tokens are Fernet-encrypted at rest
  (`ENCRYPTION_KEY`, or a key derived from `SECRET_KEY` when unset).
* Secrets are never returned by the API (`credentials_configured: true` only).
* `security.redact_secrets` strips URL userinfo, `password=`, `token:`, `api_key`,
  `Authorization: Bearer …` before anything is logged, stored in a document, or
  placed into AI context.
* The scan never indexes secret *values* as graph facts.

## Webhooks

* GitHub: `X-Hub-Signature-256` HMAC-SHA256 over the raw body, constant-time compare.
* GitLab: `X-Gitlab-Token` constant-time compare.
* Delivery IDs are unique in `webhook_events`; replays return `duplicate` without work.
* Branch scoping: pushes to other branches are ignored.
* The payload is **not** trusted as content. Only the commit SHA is read; the
  scanner fetches the repository through its Git adapter, so a forged payload cannot
  inject code into the graph.

## SSRF and outbound access

`validate_outbound_url` rejects non-HTTP(S) schemes, loopback, link-local
(169.254.0.0/16, cloud metadata), private, reserved and multicast addresses unless
`ALLOW_PRIVATE_NETWORKS=true`. It is applied to AI provider base URLs, Jira and
Confluence endpoints, and remote Git URLs. Local repository paths are gated by
`ALLOW_LOCAL_REPOS` (enabled for the self-hosted demo).

## Data minimisation for AI

* Only the selected target's facts, edges, and bounded source excerpts are sent.
* Context is character-capped (`AI_MAX_CONTEXT_CHARS`) and redacted.
* No repository credentials, environment values, or unrelated services are included.
* Provider, model, prompt version, revision and token counts are stored per insight.
* Local providers (Ollama) are first-class for air-gapped deployments.

## Audit

`audit_events` records authentication, workspace/repository changes, scans, scans
failed, webhook deliveries, AI requests/approvals/rejections/edits, API key
creation, integration changes and every MCP tool call (actor_type=`mcp`).

## Known hardening gaps

* Rate limiting is in-process; run one API replica or put a shared limiter in front.
* No 2FA / SSO yet.
* No per-repository deploy keys; repository tokens are per repository and encrypted.
* Container images run as a non-root user, but the scanner is not sandboxed beyond
  filesystem isolation of the checkout volume (no seccomp profile shipped).
