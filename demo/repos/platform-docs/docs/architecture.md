# Runtime architecture

```
Client
  |
  v
booking-gateway (Spring Cloud Gateway)
  |-- /api/reservations/** --> reservation-service
  |-- /api/payments/**     --> payment-service
  |-- /api/users/**        --> user-service

reservation-service --HTTP--> payment-service
reservation-service --Kafka booking.created--> order-service
order-service --HTTP--> inventory-service
order-service --Kafka order.created--> inventory-service
payment-service --Kafka payment.completed--> invoice-service
payment-service --Kafka payment.completed--> notification-service
```

## Availability and failure handling

reservation-service treats a declined authorisation as a business failure and
rolls back the reservation. payment-service retries stale authorisations every
five minutes with a scheduled job. invoice-service is event-driven and therefore
eventually consistent: an invoice appears after capture, not before.

## Non-functional notes

- Redis caches room availability for 10 minutes in inventory-service.
- notification-service stores the last notification per guest in Redis.
- Stripe is an external dependency of payment-service.
