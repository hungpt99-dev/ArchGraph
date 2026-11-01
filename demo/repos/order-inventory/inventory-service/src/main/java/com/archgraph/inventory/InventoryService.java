package com.archgraph.inventory;

import org.springframework.cache.annotation.Cacheable;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class InventoryService {

    private final RoomRepository roomRepository;

    public InventoryService(RoomRepository roomRepository) {
        this.roomRepository = roomRepository;
    }

    @Cacheable(value = "roomAvailability", key = "#roomId")
    @Transactional(readOnly = true)
    public AvailabilityResponse check(Long roomId, int nights) {
        Room room = roomRepository.findByRoomId(roomId).orElseThrow();
        return new AvailabilityResponse(room.getAvailableUnits() > 0, room.getAvailableUnits());
    }

    @Transactional
    public AvailabilityResponse reserve(ReserveRequest request) {
        Room room = roomRepository.findByRoomId(request.roomId()).orElseThrow();
        room.reserve(request.nights());
        roomRepository.save(room);
        return new AvailabilityResponse(room.getAvailableUnits() > 0, room.getAvailableUnits());
    }

    @KafkaListener(topics = "order.created", groupId = "inventory-group")
    @Transactional
    public void onOrderCreated(String payload) {
        roomRepository.findAll().forEach(room -> room.touch());
    }
}
