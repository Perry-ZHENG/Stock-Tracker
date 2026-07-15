CREATE TABLE provider_credit_reservations (
    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_name TEXT NOT NULL,
    reserved_at TEXT NOT NULL,
    credits INTEGER NOT NULL CHECK (credits > 0)
);

CREATE INDEX idx_provider_credit_reservations_provider_time
ON provider_credit_reservations(provider_name, reserved_at);
