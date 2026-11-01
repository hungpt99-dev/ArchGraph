package com.archgraph.reservation;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

@FeignClient(name = "payment-service", url = "${services.payment.url}")
public interface PaymentClient {

    @PostMapping("/api/payments/authorize")
    PaymentAuthorization authorize(@RequestBody PaymentRequest request);

    @PostMapping("/api/payments/refund")
    RefundResponse refund(@RequestBody RefundRequest request);
}
