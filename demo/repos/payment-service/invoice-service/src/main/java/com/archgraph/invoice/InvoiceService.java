package com.archgraph.invoice;

import com.archgraph.shared.EventEnvelope;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.util.List;

@Service
public class InvoiceService {

    private final InvoiceRepository invoiceRepository;

    public InvoiceService(InvoiceRepository invoiceRepository) {
        this.invoiceRepository = invoiceRepository;
    }

    @KafkaListener(topics = "payment.completed", groupId = "invoice-group")
    @Transactional
    public void onPaymentCompleted(String payload) {
        Long paymentId = Long.parseLong(payload.trim());
        invoiceRepository.save(new Invoice(paymentId, "GENERATED"));
    }

    @Transactional(readOnly = true)
    public InvoiceResponse get(Long id) {
        return InvoiceResponse.from(invoiceRepository.findById(id).orElseThrow());
    }

    @Transactional(readOnly = true)
    public List<InvoiceResponse> findByReservation(Long reservationId) {
        return invoiceRepository.findByReservationId(reservationId).stream()
                .map(InvoiceResponse::from).toList();
    }
}
