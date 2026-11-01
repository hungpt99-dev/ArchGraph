package com.archgraph.shared;

import java.time.Instant;

public record EventEnvelope(String eventType, Long aggregateId, Instant occurredAt) {
    public boolean isValid() {
        return eventType != null && aggregateId != null;
    }
}
