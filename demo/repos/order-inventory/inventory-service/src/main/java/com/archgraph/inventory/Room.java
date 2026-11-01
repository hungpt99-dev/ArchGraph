package com.archgraph.inventory;

import jakarta.persistence.*;

@Entity
@Table(name = "rooms")
public class Room {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private Long roomId;
    private String roomType;
    private int availableUnits;

    public Room() {}

    public void reserve(int nights) {
        this.availableUnits = Math.max(0, this.availableUnits - 1);
    }

    public void touch() { /* inventory projection touch */ }
    public Long getRoomId() { return roomId; }
    public int getAvailableUnits() { return availableUnits; }
}
