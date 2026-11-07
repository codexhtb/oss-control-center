# OSS Control Center — ISP operations, redesigned

**Engineering case study | Network Engineering × Platform Engineering × Workflow Automation**  
**2026 · FastAPI · React/TypeScript · PostgreSQL · NetUP · LibreNMS · Telegram**

[← Repository](../README.md) · [Architecture notes](architecture/current-system.md) · [Staff chat](architecture/staff-chat.md)

> **Public case study.** This explains the workflow, design decisions, and capabilities represented in the code and internal demonstrations. It is not a production deployment runbook or a formal availability/security audit. Older technical documents in this repository may reflect an October 2026 dev/LAB snapshot rather than the latest configuration.

## Why this exists

A single support call at an internet service provider used to involve several unrelated tools: billing (NetUP UTM5), a legacy ServiceDesk, an older monitoring interface, direct OLT/CLI diagnostics, and messenger-based field dispatch.

People acted as the integration layer. The operator copied billing data into a ticket; the engineer repeated the lookup to discover the network connection; field reports returned by chat and had to be copied back before a customer callback could close the incident.

**The goal was to remove duplicated work and context switching without automating away an engineer's technical judgment.**

## The workflow: before and after

| Stage | Before | With OSS Control Center |
| --- | --- | --- |
| Identify subscriber | Search NetUP, manually transfer details | Search **only by account number**, read billing context |
| Register issue | Fill a separate legacy ServiceDesk | Create a subscriber-linked ticket in the same workspace |
| Locate connection | Repeat the account lookup, then manually find OLT/port/VLAN | Show service and network context alongside the ticket |
| Investigate | Switch to network CLI and separate PRTG/monitoring | Read-only device/ONU observations with state, optical values, reasons and timestamps |
| Request field work | Copy details into a messenger | Engineer reviews and explicitly sends a structured Telegram task |
| Receive field report | Read chat and retype the result | The Telegram report is linked to the originating ticket |
| Close the case | Fragmented handoff, manual reconciliation | Engineer resolution → customer callback → close or reopen |
| Team communication | External messenger threads | Shared staff chat inside the authenticated workspace |

### End-to-end case lifecycle

~~~mermaid
flowchart TD
    A["Subscriber calls"] --> B["Operator: account-number lookup"]
    B --> C["Subscriber-linked ticket"]
    C --> D["Engineer reviews service and diagnostics"]
    D --> E{"Field visit?"}
    E -->|Yes| F["Explicit Telegram field dispatch"]
    F --> G["Field worker submits report"]
    G --> H["Engineer confirms work"]
    E -->|No| I["Engineer records resolution"]
    H --> I
    I --> J["Customer confirmation callback"]
    J -->|Restored| K["Ticket closed"]
    J -->|Still broken| D
    J -->|No answer| L["Callback remains pending"]
~~~

An **ONU observation is evidence, not a final diagnosis**. A telegram acknowledgment is not a customer-confirmed resolution. Each transition is recorded by the OSS application.

## Architecture at a glance

~~~mermaid
flowchart LR
    PEOPLE["Operators · Engineers · Callback staff"] --> WEB["React / TypeScript UI"]
    WEB --> API["FastAPI"]
    API <--> DB[("PostgreSQL<br/>tickets · audit · chat")]
    API -->|Read-only| BILL[("NetUP UTM5<br/>billing source / replica")]
    API -->|Read-only| LIB["LibreNMS API"]
    API -->|Read-only| ONU[("BDCOM ONU observations<br/>LibreNMS MariaDB")]
    API -->|Field dispatch / report| BOT["Telegram bot"]
    API -->|SSE| WEB
~~~

**Data ownership and trust boundaries**

- **OSS PostgreSQL** owns tickets, status transitions, staff chat, audit history, and ticket snapshots.
- **NetUP** supplies subscriber identity, account, finances, service and connection context; the OSS provider reads data rather than modifying the billing system.
- **LibreNMS / BDCOM collectors** supply network observations; OSS does not trigger a fresh device SNMP poll when a ticket is opened.
- **Telegram** carries structured field dispatch and responses; the bot does not unilaterally close tickets.
- **API permissions, session checks, CSRF protection and audit trails** are enforced server-side, not inferred from hidden UI controls.

The exact billing and monitoring connections depend on the deployed environment; source code and dev runbooks must not be interpreted as proof of production connectivity.

## What was built

### Subscriber 360 and billing integration

Operators search by **account number**, not by name or phone number. The subscriber workspace brings together contact information, address, linked services, network connection, account balance, payment/charge history and earlier tickets. Financial views are access-controlled and sourced from billing read-only data.

### Network-aware diagnosis

The service and subscriber's known network association provide context for monitoring results. The integration displays available OLT/PON/ONU observations, optical measurements and documented disconnection reasons such as \`wire-down\`.

Correlation is deliberately conservative: a shared VLAN, ambiguous network mapping or stale observation must **not** be presented as a proven unique subscriber-to-ONU match. Missing evidence is shown as unknown/unconfirmed rather than fabricated.

### Service Desk and customer callback

Tickets have assigned responsibilities, comments, action history, engineer resolution, and a separate callback workflow. The result of the customer follow-up determines whether a case can close or needs further investigation.

### Telegram field dispatch

The engineer explicitly creates and sends the field request. The integrated bot can capture a completion report and attach it to the original case. Telegram delivery, field-work reporting, engineering resolution and customer closure are kept as separate events.

### Staff chat

An internal room serves operators and engineering staff, with persistent messages, individual read state, authenticated endpoints and event updates. Chat is for collaboration: it does **not** replace formal subscriber tickets.

## Engineering decisions that mattered

1. **Rebuild the workflow, not just the UI.** The biggest gain comes from removing repeated lookups and manual transfer between systems.
2. **Treat sources of truth separately.** Billing data, network telemetry, and ticket decisions are distinct records with different lifecycles.
3. **Make uncertainty visible.** A stale ONU measurement or ambiguous association is not a confident diagnosis.
4. **Avoid side effects in external integrations.** Billing/monitoring access is read-only; sending a field request is explicit.
5. **Keep state changes auditable.** Telegram reports and chat messages do not silently resolve or close support tickets.
6. **Design for everyday users.** The contact center primarily needs three sections: tickets, subscriber lookup, and internal chat.

## Technology and repository map

| Area | Implementation |
| --- | --- |
| Frontend | [React / TypeScript](../frontend/src/) |
| Backend API | [FastAPI](../app/api/v1/) |
| Billing adapter | [Billing module](../app/modules/billing/) |
| Monitoring and ONU correlation | [Diagnostics module](../app/modules/diagnostics/) |
| Ticket lifecycle | [Tickets module](../app/modules/tickets/) |
| Staff chat | [Chat module](../app/modules/chat/) |
| Telegram handoff | [Field dispatch module](../app/modules/field_dispatch/) |
| Data | PostgreSQL and Alembic migrations |
| Development/runtime | Docker Compose, isolated configuration and runbooks |
| Tests | [Backend test suite](../tests/), [frontend tests](../frontend/src/pages/) |

## Outcomes and evidence

The implemented design consolidates subscriber lookup, ticket creation, network context, field dispatch, engineer resolution and callback follow-up in a single support flow. This reduces the *number of manual handoffs by design*; exact time savings, uptime, SLA effects and cost reduction have **not yet been measured** and are not claimed here.

The broader network-observability system monitored thousands of EPON/GPON ONU devices in a dated snapshot. That is a point-in-time inventory observation, **not** a claimed OSS platform uptime percentage or a permanent online/offline ratio.

## Publication and privacy

This page intentionally avoids real subscriber names, account numbers, phone numbers, live management IP addresses, private device mappings, and production credentials. Screen captures should be published **only after** anonymization and confirmation that the organization permits public use.

**Next portfolio material:** sanitized UI screenshots, a small end-to-end demo using synthetic subscriber data, and measured before/after operational metrics.

---

*Scope: a technical portfolio case study, not a license grant, production installation guide or endorsement by the employer.*
