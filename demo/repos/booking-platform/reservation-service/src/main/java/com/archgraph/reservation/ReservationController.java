package com.archgraph.reservation;

import org.springframework.web.bind.annotation.*;
import java.util.List;

@RestController
@RequestMapping("/api/reservations")
public class ReservationController {

    private final ReservationService reservationService;

    public ReservationController(ReservationService reservationService) {
        this.reservationService = reservationService;
    }

    @PostMapping
    @PreAuthorize("hasRole('GUEST')")
    public ReservationResponse create(@Valid @RequestBody CreateReservationRequest request) {
        return reservationService.createReservation(request);
    }

    @GetMapping("/{id}")
    public ReservationResponse get(@PathVariable Long id) {
        return reservationService.get(id);
    }

    @DeleteMapping("/{id}")
    @PreAuthorize("hasRole('GUEST') or hasRole('ADMIN')")
    public void cancel(@PathVariable Long id) {
        reservationService.cancel(id);
    }

    @GetMapping("/search")
    public List<ReservationResponse> search(@RequestParam String guestEmail) {
        return reservationService.searchByGuest(guestEmail);
    }
}
