package com.archgraph.payment;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import java.util.List;
import java.util.Optional;

public interface PaymentRepository extends JpaRepository<Payment, Long> {

    Optional<Payment> findByReservationId(Long reservationId);

    @Query(value = "select * from payments where status = 'AUTHORIZED' and created_at < now() - interval '2 days'", nativeQuery = true)
    List<Payment> findStaleAuthorizations();
}
