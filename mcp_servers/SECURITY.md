# Security Posture & Threat Model — l10n-opus-id-search MCP

> Scope: `mcp_servers/opus_search_mcp.py` (RingCentral MCP Directory display name
> **l10n-opus-id-search**, Service ID `0fuhlovz57gc`). This document supports the AppSec
> review (**ASCON-1781**, threat modeling **ASCON-1782**) and serves as the SECURITY note
> for this MCP. All conclusions were verified against source.

## TL;DR
Read-only, offline, stdio (local) execution, no auth, **zero outbound network**, fully
parameterized SQL, no arbitrary-file read. The attack surface is close to the minimum a
MCP can have. What needs governance is not what it *can do* (almost nothing) but what it
*returns* — internal translation content + repo coordinates — classified
**Internal/Confidential**, hence restricted to the Localization team's local, controlled clients.

## Data classification (key review item)
The index `~/.tranzor_exporter/opus_index.db` queried by this MCP is classified
**RingCentral Internal / Confidential**:
- **Contains**: real product UI source strings + latest translations in all locales (may
  include pre-release feature copy, customer brand strings, and EU-locale translations),
  plus internal GitLab paths / source-file paths / MR metadata.
- **Does NOT contain**: JWTs / API keys / passwords / any PII.

## Data flow & trust boundaries
```
[Trust boundary A: local process space]
 MCP client ──stdio──▶ opus_search_mcp.py  (FastMCP "opus-search")
 (Claude Desktop/Code/      │ @tool opus_search_translations (9 search params)
  controlled agent)         ▼
                  opus_search.search_index()
                    · requires >=1 narrowing condition · limit <= 2000
                    · all SQL ?-parameterized · LIKE metacharacters escaped
                            ▼
                  opus_id_monitor._connect / init_db   (SELECT only; idempotent first-run DDL)
                            ▼
                  ~/.tranzor_exporter/opus_index.db     (local SQLite, plaintext / WAL)

 ✗ no outbound network   ✗ no listening port   ✗ reads no file other than the default DB
 Index is populated offline by opus_id_monitor.sync_* (this MCP never triggers sync)
 Source: Tranzor MR Pipeline / Scan Task / File Translation
```
- **Boundary A (stdio, inter-process)**: the only input entry point = the 9 search params,
  all constrained by parameterized SQL.
- **Boundary B (code integrity)**: on startup the server `sys.path.insert`s and imports the
  whole my-tools repo (`opus_search → opus_id_monitor`). The **real trust boundary = who can
  write the repo** → control: enforced PR review + protected branch.
- **Boundary C (data at rest)**: local plaintext SQLite, no file-level ACL, protected by host
  disk encryption.

## Exposed fields & classification
| Field | Content | Classification |
|---|---|---|
| `source_text` / `translated_text` | Real UI source + latest translations in all locales (incl. pre-release; each truncated to 8192 chars) | **Confidential** |
| `project_id` / `source_file_path` / `mr_iid` / `task_id` / `release` | GitLab path / real source-file relative path / release-pipeline metadata | Internal |
| `opus_id` / `alias` / `logical_key` / `first_seen` | Identifiers & timestamps | Internal |
| JWT / API key / password / PII | **Not present in schema** | — |

## Existing controls (quote directly)
- **Read-only**: the only reachable path is `SELECT`; the sole side effect is an idempotent
  first-run table-creation DDL (local cache schema); it never writes Tranzor platform data.
- **No egress**: no `requests/urllib/http/socket/httpx/smtp/webhook/telemetry` calls on any
  reachable code path; no telemetry.
- **Parameterized SQL + LIKE escaping**: all 7 WHERE conditions use `?` placeholders;
  `_esc()` escapes `\ % _` with `ESCAPE '\'`. No injection, no wildcard abuse.
- **Narrowing + cap**: a missing narrowing condition raises `ValueError`;
  `limit = max(1, min(int(limit), 2000))`; `GROUP BY opus_id` with `limit+1` before expansion,
  resistant to resource exhaustion.
- **No secrets**: this MCP reads no token / Authorization / secret env var (only the offline
  sync path uses a JWT, which this MCP never calls).
- **stdio, no listening port**: the `command` model is launched locally on demand and exits
  after use; no network listener.
- **No `db_path` exposure**: the MCP tool deliberately does not pass through `db_path`, so the
  DB path is always the default — no arbitrary-file read / DB redirection; `_connect` is only
  `sqlite3.connect` (no `ATTACH`/`load_extension`).
- **Minimal dependencies**: besides stdlib, the only third-party package on the reachable path
  is `mcp` (see [`requirements.txt`](requirements.txt), pinned `==1.28.1`).

## Residual risks & mitigations
| Residual risk | Mitigation |
|---|---|
| Returned content includes confidential translations + internal coordinates; if called by a wide-area LLM client it enters model context → pre-release leak | Data classification + restrict to Localization-team local controlled clients; **not in wide-area Directory** |
| DB stored as plaintext on a personal endpoint, no file-level encryption | Host disk encryption (BitLocker) + not exfiltrated + cleared on offboarding/device change |
| Third-party `mcp` supply chain | `requirements.txt` pins the version; recommend `pip --require-hashes` + `pip-audit` |
| Code integrity = repo write governance | Enforced PR review + protected branch + CODEOWNERS |
| `.mcp.json` uses bare `python` + relative path | Known low risk (local tool, clone-and-run convenience); can pin to a venv interpreter absolute path if stricter control is required |

## Constraints we accept
1. Data Internal/Confidential; Localization-team local controlled clients only; **not
   registered to any external/cross-team wide-area Directory**.
2. Keep **stdio/local**; no HTTP/SSE listening endpoint.
3. Host disk encryption + DB not exfiltrated + cleared on offboarding/device change.
4. Dependency **pinned** (`requirements.txt`); updates reviewed.
5. my-tools repo: **enforced PR review + protected branch**.
