package com.archgraph.order;

import jakarta.persistence.*;
import java.math.BigDecimal;

@Entity
@Table(name = "orders")
public class Order {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private Long reservationId;
    private Long roomId;
    private BigDecimal amount;
    private String status;

    public Order() {}

    public Order(Long reservationId, Long roomId, BigDecimal amount) {
        this.reservationId = reservationId;
        this.roomId = roomId;
        this.amount = amount;
        this.status = "CONFIRMED";
    }

    public Long getId() { return id; }
    public String getStatus() { return status; }
}
