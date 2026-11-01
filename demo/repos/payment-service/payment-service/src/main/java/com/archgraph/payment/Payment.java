package com.archgraph.payment;

import com.archgraph.shared.Money;
import jakarta.persistence.*;
import java.math.BigDecimal;

@Entity
@Table(name = "payments")
public class Payment {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private Long reservationId;
    private BigDecimal amount;
    private String currency;
    private String status;
    private String reference;

    public Payment() {}

    public Payment(Long reservationId, Money money) {
        this.reservationId = reservationId;
        this.amount = money.getAmount();
        this.currency = money.getCurrency();
        this.status = "PENDING";
    }

    public void markAuthorized(String reference) {
        this.reference = reference;
        this.status = "AUTHORIZED";
    }

    public void capture() { this.status = "CAPTURED"; }
    public void refund() { this.status = "REFUNDED"; }
    public Long getId() { return id; }
    public BigDecimal getAmount() { return amount; }
    public String getCurrency() { return currency; }
    public String getReference() { return reference; }
}
