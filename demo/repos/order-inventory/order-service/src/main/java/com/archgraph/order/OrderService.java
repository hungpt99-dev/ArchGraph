package com.archgraph.order;

import com.archgraph.shared.EventEnvelope;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.time.Instant;

@Service
public class OrderService {

    private static final String ORDER_CREATED = "order.created";

    private final OrderRepository orderRepository;
    private final InventoryClient inventoryClient;
    private final KafkaTemplate<String, Object> kafkaTemplate;

    public OrderService(OrderRepository orderRepository, InventoryClient inventoryClient,
                        KafkaTemplate<String, Object> kafkaTemplate) {
        this.orderRepository = orderRepository;
        this.inventoryClient = inventoryClient;
        this.kafkaTemplate = kafkaTemplate;
    }

    @Transactional
    public OrderResponse place(CreateOrderRequest request) {
        Availability availability = inventoryClient.check(request.roomId(), request.nights());
        if (!availability.available()) {
            throw new IllegalStateException("Room not available");
        }
        Order order = new Order(request.reservationId(), request.roomId(), request.amount());
        Order saved = orderRepository.save(order);
        kafkaTemplate.send(ORDER_CREATED, saved.getId().toString(),
                new EventEnvelope("order.created", saved.getId(), Instant.now()));
        return OrderResponse.from(saved);
    }

    @KafkaListener(topics = "booking.created", groupId = "order-service")
    @Transactional
    public void onBookingCreated(String payload) {
        // Creates the folio order for a confirmed booking.
        orderRepository.save(new Order(Long.parseLong(payload.trim()), 0L, java.math.BigDecimal.ZERO));
    }

    @Transactional(readOnly = true)
    public OrderResponse get(Long id) {
        return OrderResponse.from(orderRepository.findById(id).orElseThrow());
    }
}
