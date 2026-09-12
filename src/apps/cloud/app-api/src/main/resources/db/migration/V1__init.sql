CREATE TABLE member (
    id         BIGSERIAL PRIMARY KEY,
    nickname   VARCHAR(50) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE forecast (
    id              BIGSERIAL PRIMARY KEY,
    ticker          VARCHAR(20) NOT NULL,
    direction       VARCHAR(20) NOT NULL,
    rationale       TEXT        NOT NULL,
    status          VARCHAR(20) NOT NULL,
    end_at          TIMESTAMPTZ NOT NULL,
    withdraw_reason TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_forecast_status ON forecast (status);

CREATE TABLE vote (
    id          BIGSERIAL PRIMARY KEY,
    forecast_id BIGINT      NOT NULL REFERENCES forecast (id),
    user_id     BIGINT      NOT NULL REFERENCES member (id),
    choice      VARCHAR(10) NOT NULL,
    voided      BOOLEAN     NOT NULL DEFAULT false,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (forecast_id, user_id)
);

CREATE INDEX idx_vote_user ON vote (user_id);

CREATE TABLE outbox (
    id           BIGSERIAL PRIMARY KEY,
    event_type   VARCHAR(20) NOT NULL,
    forecast_id  BIGINT      NOT NULL,
    payload      TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ
);

CREATE TABLE redis_rebuild (
    id            BIGSERIAL PRIMARY KEY,
    resource_type VARCHAR(20) NOT NULL,
    resource_id   VARCHAR(50) NOT NULL,
    status        VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    requested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (resource_type, resource_id)
);

INSERT INTO member (nickname)
VALUES ('demo-user-1'),
       ('demo-user-2'),
       ('demo-user-3');
