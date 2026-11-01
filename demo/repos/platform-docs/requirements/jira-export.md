# Jira requirements export (fixture)

## HOTEL-142 - Guests must be able to cancel a reservation and receive a refund

As a guest I want to cancel my reservation so that I am refunded automatically.

Acceptance criteria:
- Cancelling a reservation publishes `booking.cancelled`.
- payment-service consumes the event and refunds the authorisation.
- The reservation status becomes CANCELLED.
- notification-service sends a cancellation email.

## HOTEL-201 - Invoice must be generated after payment capture

Acceptance criteria:
- Capturing a payment publishes `payment.completed`.
- invoice-service creates an invoice row for the payment.
- The invoice is retrievable through GET /api/invoices/{id}.

## HOTEL-233 - Prevent double booking of the last room

Acceptance criteria:
- inventory-service availability check must be atomic.
- order-service must not create an order when availability is false.
