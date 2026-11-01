package com.archgraph.payment;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import java.util.List;

@Component
public class PaymentRetryJob {

    private final PaymentRepository paymentRepository;
    private final PaymentService paymentService;

    public PaymentRetryJob(PaymentRepository paymentRepository, PaymentService paymentService) {
        this.paymentRepository = paymentRepository;
        this.paymentService = paymentService;
    }

    @Scheduled(fixedDelay = 300000)
    public void retryStaleAuthorizations() {
        List<Payment> stale = paymentRepository.findStaleAuthorizations();
        for (Payment payment : stale) {
            paymentService.capture(payment.getId());
        }
    }
}
