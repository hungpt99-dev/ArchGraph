package com.archgraph.reservation;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import java.util.List;

@Component
public class ReservationMaintenanceJob {

    private final ReservationRepository reservationRepository;

    public ReservationMaintenanceJob(ReservationRepository reservationRepository) {
        this.reservationRepository = reservationRepository;
    }

    @Scheduled(cron = "0 0 3 * * *")
    @Transactional
    public void expireStaleReservations() {
        List<Reservation> stale = reservationRepository.findStalePending();
        for (Reservation reservation : stale) {
            reservation.cancel();
            reservationRepository.save(reservation);
        }
    }
}
