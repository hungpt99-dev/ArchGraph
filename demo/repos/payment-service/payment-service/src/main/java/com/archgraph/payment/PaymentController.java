package com.archgraph.payment;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/payments")
public class PaymentController {

    private final PaymentService paymentService;

    public PaymentController(PaymentService paymentService) {
        this.paymentService = paymentService;
    }

    @PostMapping("/authorize")
    @PreAuthorize("hasRole('SERVICE')")
    public PaymentAuthorization authorize(@Valid @RequestBody PaymentRequest request) {
        return paymentService.authorize(request);
    }

    @PostMapping("/capture/{id}")
    public PaymentResponse capture(@PathVariable Long id) {
        return paymentService.capture(id);
    }

    @PostMapping("/refund")
    public RefundResponse refund(@RequestBody RefundRequest request) {
        return paymentService.refund(request);
    }

    @GetMapping("/{id}")
    public PaymentResponse get(@PathVariable Long id) {
        return paymentService.get(id);
    }
}
