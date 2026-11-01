package com.archgraph.invoice;

import jakarta.persistence.*;

@Entity
@Table(name = "invoices")
public class Invoice {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private Long paymentId;
    private Long reservationId;
    private String status;

    public Invoice() {}

    public Invoice(Long paymentId, String status) {
        this.paymentId = paymentId;
        this.status = status;
    }

    public Long getId() { return id; }
    public Long getReservationId() { return reservationId; }
    public String getStatus() { return status; }
}
