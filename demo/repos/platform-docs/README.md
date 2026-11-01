# Hotel Platform - Architecture Documentation

This repository holds the architecture documentation for the Hotel Management
Platform, a sample microservice estate used by the ArchGraph demo workspace.

## System overview

The platform has eight services across four code repositories:

| Service | Repository | Responsibility |
| --- | --- | --- |
| booking-gateway | booking-platform | Public API gateway, routes to reservation, payment and user services |
| user-service | booking-platform | Guest profiles and loyalty tier |
| reservation-service | booking-platform | Booking lifecycle, room availability checks, payment authorisation |
| payment-service | payment-service | Card authorisation through Stripe, refunds, retries |
| invoice-service | payment-service | Invoice generation from payment events |
| order-service | order-inventory | Folio orders for a reservation |
| inventory-service | order-inventory | Room inventory and availability cache |
| notification-service | notification-service | Guest notifications over email |

## Event backbone

Kafka topics:

- `booking.created` - published by reservation-service, consumed by order-service.
- `booking.cancelled` - published by reservation-service, consumed by payment-service.
- `payment.completed` - published by payment-service, consumed by invoice-service and notification-service.
- `payment.refunded` - published by payment-service.
- `order.created` - published by order-service, consumed by inventory-service.
- `notification.sent` - published by notification-service.

## Data ownership

Reservations live in the `booking` PostgreSQL database. Payments own the
`payments` database, invoices the `invoices` database. No service reads another
service's tables directly; access is always through HTTP or Kafka.
