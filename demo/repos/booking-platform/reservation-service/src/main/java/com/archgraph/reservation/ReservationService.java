package com.archgraph.reservation;

import com.archgraph.shared.EventEnvelope;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.time.Instant;
import java.util.List;

@Service
public class ReservationService {

    private static final String BOOKING_TOPIC = "booking.created";
    private static final String CANCELLATION_TOPIC = "booking.cancelled";

    private final ReservationRepository reservationRepository;
    private final PaymentClient paymentClient;
    private final KafkaTemplate<String, Object> kafkaTemplate;

    public ReservationService(ReservationRepository reservationRepository,
                              PaymentClient paymentClient,
                              KafkaTemplate<String, Object> kafkaTemplate) {
        this.reservationRepository = reservationRepository;
        this.paymentClient = paymentClient;
        this.kafkaTemplate = kafkaTemplate;
    }

    @Transactional
    public ReservationResponse createReservation(CreateReservationRequest request) {
        Reservation reservation = new Reservation(request.guestEmail(), request.roomId(),
                request.checkIn(), request.checkOut());
        Reservation saved = reservationRepository.save(reservation);
        PaymentAuthorization authorization = paymentClient.authorize(
                new PaymentRequest(saved.getId(), request.amount(), request.currency()));
        if (!authorization.approved()) {
            throw new IllegalStateException("Payment declined for reservation " + saved.getId());
        }
        kafkaTemplate.send(BOOKING_TOPIC, saved.getId().toString(),
                new EventEnvelope("booking.created", saved.getId(), Instant.now()));
        return ReservationResponse.from(saved);
    }

    @Transactional
    public void cancel(Long id) {
        Reservation reservation = reservationRepository.findById(id).orElseThrow();
        reservation.cancel();
        reservationRepository.save(reservation);
        paymentClient.refund(new RefundRequest(id, reservation.getAmount()));
        kafkaTemplate.send(CANCELLATION_TOPIC, id.toString(),
                new EventEnvelope("booking.cancelled", id, Instant.now()));
    }

    @Transactional(readOnly = true)
    public ReservationResponse get(Long id) {
        return ReservationResponse.from(reservationRepository.findById(id).orElseThrow());
    }

    @Transactional(readOnly = true)
    public List<ReservationResponse> searchByGuest(String guestEmail) {
        return reservationRepository.findByGuestEmail(guestEmail).stream()
                .map(ReservationResponse::from).toList();
    }
}
