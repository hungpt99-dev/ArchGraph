package com.archgraph.reservation;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.time.LocalDate;

@Entity
@Table(name = "reservations")
public class Reservation {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String guestEmail;
    private Long roomId;
    private LocalDate checkIn;
    private LocalDate checkOut;
    private BigDecimal amount;
    private String status;

    public Reservation() {}

    public Reservation(String guestEmail, Long roomId, LocalDate checkIn, LocalDate checkOut) {
        this.guestEmail = guestEmail;
        this.roomId = roomId;
        this.checkIn = checkIn;
        this.checkOut = checkOut;
        this.status = "PENDING_PAYMENT";
    }

    public void cancel() {
        this.status = "CANCELLED";
    }

    public Long getId() { return id; }
    public BigDecimal getAmount() { return amount; }
    public String getStatus() { return status; }
}
