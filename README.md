# OSS Control Center

### ISP Service Desk · Subscriber 360 · Network Diagnostics · Field Dispatch

**An ISP operations platform designed to replace repeated lookups, disconnected monitoring screens, and manual messenger handoffs with one support workflow.**

**[📖 Read the engineering case study →](docs/portfolio-case-study.md)** — *the problem, architecture, before/after workflow, design decisions, and documented outcomes.*

> **Status (07 November 2026):** This repository contains the evolving implementation and an internally demonstrated workflow. Features listed below are present in the code or project demonstrations, but this README does **not** certify production deployment, availability, complete operational security, or the activation of every external integration. Some existing detailed docs and runbooks reflect the **17 October 2026 dev/LAB snapshot** and must be revalidated before use.

## The operational problem

The old process involved NetUP UTM5 → ServiceDesk Plus → equipment CLI / PRTG → messenger → ServiceDesk Plus. Operators copied subscriber details, engineers repeated the same lookup, and field responses had to be transcribed manually.

**OSS Control Center integrates these stages without treating monitoring observations as engineer decisions.**

## Main capabilities

| Capability | What the implementation provides |
| --- | --- |
| **Subscriber 360** | Lookup **by account number**, contacts, address, service links, connection context, previous tickets |
| **Billing read-only** | NetUP-backed subscriber data, balance, payment and charge history, services and tariff information (source depends on environment) |
| **Service Desk** | Subscriber-linked, manual and internal tickets; wrong-payment ticket type; assignment, comments, audit and histories |
| **Network diagnostics** | Read-only LibreNMS/BDCOM EPON-GPON observations, ONU state and optical measurements with conservative correlation |
| **Field work** | Explicit Telegram outbound handoff and incoming field report handling; separate engineer confirmation |
| **Customer callback** | Follow-up queue and customer-confirmed closure or return to investigation |
| **Internal chat** | Authenticated shared room for operators and engineers, message history/read state and SSE updates |
| **Administration** | Users, roles, permissions and configurable service-desk settings |

These are **software capabilities**, not claims that all external services are currently configured or reachable in production. In particular, older repository documentation describes an isolated NetUP LAB source and Telegram LAB integration; verify the current runtime environment independently. An Icinga integration mentioned in design documents should not be assumed active.

## Workflow

~~~mermaid
flowchart LR
    CALL["Subscriber calls"] --> ACCOUNT["Account-number lookup"]
    ACCOUNT --> TICKET["Create ticket"]
    TICKET --> DIAG["Engineer + read-only diagnosis"]
    DIAG --> FIELD{"Visit needed?"}
    FIELD -->|Yes| TG["Telegram field request / report"]
    TG --> RESOLVE["Engineer resolution"]
    FIELD -->|No| RESOLVE
    RESOLVE --> CALLBACK["Customer callback"]
    CALLBACK -->|Restored| CLOSED["Closed"]
    CALLBACK -->|Still broken| DIAG
~~~

For the fuller before/after comparison and architectural tradeoffs, see the **[engineering case study](docs/portfolio-case-study.md)**.

## Architecture

~~~mermaid
flowchart LR
    STAFF["Contact center · OSS engineers"] --> WEB["React / TypeScript"]
    WEB --> API["FastAPI"]
    API <--> PG[("PostgreSQL: tickets · chat · audit")]
    API -->|Read-only| BILL["NetUP UTM5 billing provider"]
    API -->|Read-only| LN["LibreNMS API"]
    API -->|Read-only| ONU["BDCOM ONU observations"]
    API -->|Field workflow| TG["Telegram bot"]
~~~

### Data boundaries

- **PostgreSQL OSS** holds tickets, workflow transitions, audit, chat and subscriber snapshots.
- **NetUP UTM5** supplies billing and subscriber context through read-only provider access.
- **LibreNMS/BDCOM** supplies observed network state, without turning a diagnosis screen into live device control.
- **Telegram** transports field-work messages; external reports do not close a ticket automatically.
- **Missing or ambiguous data** remains explicitly unknown/unconfirmed rather than a presumed subscriber-to-ONU match.

## Technology and code

| Layer | Source |
| --- | --- |
| React / TypeScript UI | [frontend/src](frontend/src/) |
| FastAPI routes | [app/api/v1](app/api/v1/) |
| Billing and NetUP | [app/modules/billing](app/modules/billing/) |
| Network/ONU diagnostics | [app/modules/diagnostics](app/modules/diagnostics/) |
| Tickets and lifecycle | [app/modules/tickets](app/modules/tickets/) |
| Field dispatch | [app/modules/field_dispatch](app/modules/field_dispatch/) |
| Internal chat | [app/modules/chat](app/modules/chat/) |
| Identity and permissions | [app/modules/identity](app/modules/identity/) |
| PostgreSQL migrations | [app/db/migrations/versions](app/db/migrations/versions/) |
| Backend tests | [tests](tests/) |

**Stack:** Python · FastAPI · React · TypeScript · PostgreSQL · MariaDB · Docker Compose · LibreNMS · NetUP UTM5 · Telegram Bot API.

## Documentation and development

- **[Engineering portfolio case study](docs/portfolio-case-study.md)** — recommended first read.
- [Documentation index](docs/README.md) — technical references, including older snapshots.
- [Architecture snapshot (17 Oct 2026)](docs/architecture/current-system.md) — useful historical technical baseline, not automatically the present runtime.
- [Shared staff chat](docs/architecture/staff-chat.md) — implementation and event stream.
- [Subscriber finances](docs/architecture/subscriber-360-finance-phase-2.md) — access-controlled read-only financial operations.
- [LibreNMS / BDCOM](docs/integrations/librenms-bdcom.md) — observation collection and correlation.
- [NetUP provider](docs/integrations/netup.md) — billing integration design and limitations.
- [Telegram field dispatch](docs/operations/telegram-field-dispatch.md) — dev/LAB procedures; must not be followed blindly on a different environment.
- [Secret management](docs/operations/secret-management.md) and [recovery map](docs/operations/recovery-map.md).

The Docker Compose files are environment-specific. **Do not assume that \`docker compose up\` in the repository root is a safe production installation procedure.** Keep test databases isolated from persistent operational data and verify backups before migrations.

## Security and publication

This public repository must never include billing exports, actual subscriber records, active production passwords or tokens, certificate private keys, sensitive device mappings, unredacted configuration backups, or full internal network topology. **Publishing source does not prove that every historical commit or document has been independently security-audited.**

For portfolio use, prefer sanitized screenshots and synthetic subscriber examples. No performance or financial impact figures are asserted without an actual before/after measurement.

---

*Built to make routine ISP support work less repetitive and more traceable.*
