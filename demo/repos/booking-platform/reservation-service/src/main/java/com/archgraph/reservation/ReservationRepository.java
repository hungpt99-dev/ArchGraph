package com.archgraph.reservation;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import java.util.List;

public interface ReservationRepository extends JpaRepository<Reservation, Long> {

    @Query("select r from Reservation r where r.guestEmail = ?1 order by r.checkIn desc")
    List<Reservation> findByGuestEmail(String guestEmail);

    @Query(value = "select * from reservations where status = 'PENDING_PAYMENT' and check_in < now()", nativeQuery = true)
    List<Reservation> findStalePending();
}
