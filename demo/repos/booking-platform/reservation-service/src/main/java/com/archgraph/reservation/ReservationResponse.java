package com.archgraph.reservation;

public record ReservationResponse(Long id, String guestEmail, Long roomId, String status) {
    public static ReservationResponse from(Reservation reservation) {
        return new ReservationResponse(reservation.getId(), reservation.getGuestEmail(),
                reservation.getRoomId(), reservation.getStatus());
    }
}
