CREATE TABLE forecast_vote (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    forecast_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    choice VARCHAR(10) NOT NULL,
    UNIQUE KEY uq_forecast_user (forecast_id, user_id),
    CONSTRAINT valid_choice CHECK (choice IN ('BUY', 'HOLD', 'SELL'))
);
