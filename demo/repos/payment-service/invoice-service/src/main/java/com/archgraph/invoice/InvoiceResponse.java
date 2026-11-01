package com.archgraph.invoice;

public record InvoiceResponse(Long id, Long paymentId, String status) {
    public static InvoiceResponse from(Invoice invoice) {
        return new InvoiceResponse(invoice.getId(), invoice.getPaymentId(), invoice.getStatus());
    }
}
