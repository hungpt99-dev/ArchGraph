package com.archgraph.payment;

import java.math.BigDecimal;

record PaymentRequest(Long reservationId, BigDecimal amount, String currency) {}
record PaymentResponse(Long id, String status) {
    static PaymentResponse from(Payment payment) {
        return new PaymentResponse(payment.getId(), payment.getStatus());
    }
}
record PaymentAuthorization(boolean approved, String reference) {}
record RefundRequest(Long reservationId, BigDecimal amount) {}
record RefundResponse(boolean approved, String reference) {}
record GatewayResult(boolean approved, String reference) {}
