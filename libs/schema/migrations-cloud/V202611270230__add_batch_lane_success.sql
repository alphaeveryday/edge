-- OCI 배치 cron 의 레인 성공 기록.
--
-- AWS 계정이 2026-11-29 에 닫혀 배치 레인이 OCI VM 의 cron 으로 옮겨 간다. 거기서는 운영 원장(ops_*)의
-- Planner·Reconciler 를 쓰지 않는다. 대신 레인 스크립트(infra/oci)가 모든 스텝을 exit 0 으로 끝내면 여기에
-- 한 행을 쓰고, 감시(infra/oci/vm1/watchdog.py)가 기한이 지나도 행이 없는 슬롯을 메일로 알린다.
--
-- 행이 있다 = 그 슬롯의 레인이 끝까지 성공했다. 실패·미실행 슬롯은 행이 없다(실패 사유는 통보 메일과 호스트 로그에 있다).
-- 같은 슬롯을 손으로 다시 돌리면 finished_at 만 갱신된다.
CREATE TABLE batch_lane_success (
    lane        TEXT        NOT NULL,                 -- 레인 이름(예: etf-daily, news)
    slot_at     TIMESTAMPTZ NOT NULL,                 -- 슬롯 시각(cron 예정 시각)
    finished_at TIMESTAMPTZ NOT NULL DEFAULT now(),   -- 마지막 스텝이 끝나 행을 쓴 시각
    PRIMARY KEY (lane, slot_at)
);

COMMENT ON TABLE batch_lane_success IS
'OCI 배치 cron 레인의 성공 기록. 레인이 모든 스텝을 exit 0 으로 끝내면 한 행을 쓴다. 감시가 기한이 지나도 행이 없는 슬롯을 알린다.';
