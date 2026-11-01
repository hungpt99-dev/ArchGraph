package com.archgraph.payment;

import com.archgraph.shared.EventEnvelope;
import com.archgraph.shared.Money;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.time.Instant;

@Service
public class PaymentService {

    private static final String PAYMENT_COMPLETED_TOPIC = "payment.completed";

    private final PaymentRepository paymentRepository;
    private final ExternalGatewayClient gatewayClient;
    private final KafkaTemplate<String, Object> kafkaTemplate;

    public PaymentService(PaymentRepository paymentRepository,
                          ExternalGatewayClient gatewayClient,
                          KafkaTemplate<String, Object> kafkaTemplate) {
        this.paymentRepository = paymentRepository;
        this.gatewayClient = gatewayClient;
        this.kafkaTemplate = kafkaTemplate;
    }

    @Transactional
    public PaymentAuthorization authorize(PaymentRequest request) {
        Payment payment = new Payment(request.reservationId(),
                new Money(request.amount(), request.currency()));
        GatewayResult result = gatewayClient.charge(payment.getAmount(), payment.getCurrency());
        payment.markAuthorized(result.reference());
        paymentRepository.save(payment);
        return new PaymentAuthorization(result.approved(), result.reference());
    }

    @Transactional
    public PaymentResponse capture(Long id) {
        Payment payment = paymentRepository.findById(id).orElseThrow();
        payment.capture();
        paymentRepository.save(payment);
        kafkaTemplate.send(PAYMENT_COMPLETED_TOPIC, id.toString(),
                new EventEnvelope("payment.completed", id, Instant.now()));
        return PaymentResponse.from(payment);
    }

    @Transactional
    public RefundResponse refund(RefundRequest request) {
        Payment payment = paymentRepository.findByReservationId(request.reservationId()).orElseThrow();
        GatewayResult result = gatewayClient.refund(payment.getReference(), request.amount());
        payment.refund();
        paymentRepository.save(payment);
        kafkaTemplate.send("payment.refunded", payment.getId().toString(),
                new EventEnvelope("payment.refunded", payment.getId(), Instant.now()));
        return new RefundResponse(result.approved(), result.reference());
    }

    @Transactional(readOnly = true)
    public PaymentResponse get(Long id) {
        return PaymentResponse.from(paymentRepository.findById(id).orElseThrow());
    }
}
