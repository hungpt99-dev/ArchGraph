# Onboarding

1. Clone booking-platform, payment-service, order-inventory and notification-service.
2. Run docker compose up for Postgres, Redis and Kafka.
3. Start each Spring Boot application with the `local` profile.
4. notification-service runs with `npm start`.

Known gaps: there is no runbook for replaying `payment.completed`, and the
refund flow has no automated reconciliation job. #HOTEL-142
