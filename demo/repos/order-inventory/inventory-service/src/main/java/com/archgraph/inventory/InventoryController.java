package com.archgraph.inventory;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/inventory")
public class InventoryController {

    private final InventoryService inventoryService;

    public InventoryController(InventoryService inventoryService) {
        this.inventoryService = inventoryService;
    }

    @GetMapping("/availability")
    public AvailabilityResponse availability(@RequestParam Long roomId, @RequestParam int nights) {
        return inventoryService.check(roomId, nights);
    }

    @PostMapping("/reserve")
    public AvailabilityResponse reserve(@RequestBody ReserveRequest request) {
        return inventoryService.reserve(request);
    }
}
