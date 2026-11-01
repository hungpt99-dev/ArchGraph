package com.archgraph.inventory;

record AvailabilityResponse(boolean available, int remaining) {}
record ReserveRequest(Long roomId, int nights) {}
