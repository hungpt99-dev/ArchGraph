package com.archgraph.invoice;

import org.springframework.web.bind.annotation.*;
import java.util.List;

@RestController
@RequestMapping("/api/invoices")
public class InvoiceController {

    private final InvoiceService invoiceService;

    public InvoiceController(InvoiceService invoiceService) {
        this.invoiceService = invoiceService;
    }

    @GetMapping("/{id}")
    public InvoiceResponse get(@PathVariable Long id) {
        return invoiceService.get(id);
    }

    @GetMapping("/by-reservation/{reservationId}")
    public List<InvoiceResponse> byReservation(@PathVariable Long reservationId) {
        return invoiceService.findByReservation(reservationId);
    }
}
