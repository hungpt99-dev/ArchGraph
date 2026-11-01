package com.archgraph.order;

import java.math.BigDecimal;

record CreateOrderRequest(Long reservationId, Long roomId, BigDecimal amount, int nights) {}
record OrderResponse(Long id, String status) {
    static OrderResponse from(Order order) {
        return new OrderResponse(order.getId(), order.getStatus());
    }
}
record Availability(boolean available, int remaining) {}
