# ADR-002: Database per service

Status: Accepted

## Context

Early prototypes shared one PostgreSQL instance and joined tables across
services. Schema changes caused cross-team outages.

## Decision

Each service owns its schema. Cross-service reads go through APIs. Reporting
uses a read model fed by Kafka events.

## Consequences

Reservation reporting is eventually consistent, but deployments are independent.
