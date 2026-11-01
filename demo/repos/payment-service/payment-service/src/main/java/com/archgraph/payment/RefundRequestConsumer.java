package com.archgraph.payment;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

@Component
public class RefundRequestConsumer {

    private final PaymentService paymentService;

    public RefundRequestConsumer(PaymentService paymentService) {
        this.paymentService = paymentService;
    }

    @KafkaListener(topics = "booking.cancelled", groupId = "payment-refund-group")
    public void onBookingCancelled(String payload) {
        // payload: reservationId
        Long reservationId = Long.parseLong(payload.trim());
        paymentService.refund(new RefundRequest(reservationId, null));
    }
}
