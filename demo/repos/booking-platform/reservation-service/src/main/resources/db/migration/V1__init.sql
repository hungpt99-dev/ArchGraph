CREATE TABLE reservations (
    id BIGSERIAL PRIMARY KEY,
    guest_email VARCHAR(320) NOT NULL,
    room_id BIGINT NOT NULL,
    check_in DATE NOT NULL,
    check_out DATE NOT NULL,
    amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING_PAYMENT',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_reservations_guest ON reservations (guest_email);
CREATE INDEX idx_reservations_status ON reservations (status);
