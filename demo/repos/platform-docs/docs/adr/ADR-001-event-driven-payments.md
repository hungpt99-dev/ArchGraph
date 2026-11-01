# ADR-001: Event-driven payment completion

Status: Accepted
Date: 2025-11-04
Deciders: Platform Architecture Group

## Context

Invoice generation and guest notification used to be synchronous calls from
payment-service. A notification outage failed the capture endpoint even though
the money had moved.

## Decision

payment-service publishes `payment.completed` to Kafka and returns immediately.
invoice-service and notification-service consume the event independently.

## Consequences

- Capture no longer fails when downstream services are unavailable.
- Consumers must be idempotent because Kafka delivers at least once.
- Observability must trace a payment through three services.
