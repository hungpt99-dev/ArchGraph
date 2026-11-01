# Evidence

Every folder here contains screenshots captured by Playwright from the running
application (`frontend/tests/screenshots.spec.ts`, `make screenshots`), plus the
commands and results that prove the feature works.

Start with [manifest.md](manifest.md) — it maps each feature to its implementation,
test, result and screenshots, and states plainly what is not complete.

Directory map:

| Directory | Feature |
| --- | --- |
| 01-dashboard | Login, workspace list, workspace overview |
| 02-workspaces | Services inventory and service detail |
| 03-repositories | Repository management and scan history |
| 04-scanner | Deterministic scan output (system graph) |
| 05-architecture-graph | Interactive explorer with neighbourhood expansion |
| 06-source-navigation | Exact-revision source viewer |
| 07-kafka-analysis | Topics, event trace, derived business flows |
| 08-api-analysis | REST inventory, tables, unresolved references |
| 09-documentation | Document list/search and links |
| 10-jira | Requirement fixture linked to implementation |
| 11-confluence | Documentation integration configuration |
| 12-ai-enrichment | Selective enrichment, estimate |
| 13-ai-approval | Review modal, approval queue |
| 14-git-webhooks | Webhook configuration and secret |
| 15-incremental-sync | Webhook deliveries and incremental scans |
| 16-mcp | MCP tool catalogue and connection instructions |
| 17-security | Roles, audit trail, API keys |
| 18-settings | Workspace and AI policy settings |
| 19-mobile-responsive | Mobile layout |

Raw test logs live alongside the screenshots where noted in the manifest.
