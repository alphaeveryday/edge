package com.edge.superadmin;

import com.edge.superadmin.repository.MinuteStatusRepository;
import com.edge.superadmin.repository.MinuteStatusRepository.PriceJudgmentRow;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 판정 근거 조회 SQL(§33.12 로컬) — 실 스키마에서만 확인되는 것: artifact 는 **그 job 세대**의 이력만
 * 붙고(정정된 window 의 최신 세대로 대체하지 않음), 기준선은 이 판정에 나온 종목만 실리며, 기록 없는
 * job 은 attempt NULL 로 남는다.
 */
class JdbcMinuteJudgmentIntegrationTest extends CloudPostgresIntegrationTest {

	@Autowired
	private MinuteStatusRepository repository;

	@Autowired
	private JdbcTemplate jdbc;

	@Test
	void 판정_근거는_job_세대의_이력과_관련_종목_기준선만_싣는다() {
		String sid = "judg-it";
		jdbc.update("""
				INSERT INTO minute_ingestion_session (session_id, dataset, source_group, session_date,
				       universe_version, universe_hash, expected_window_count)
				VALUES (?, 'price_minute', 'judg-it', '2026-10-05', 'u', 'h', 2)""", sid);
		for (int i = 0; i < 2; i++) {
			jdbc.update("""
					INSERT INTO minute_ingestion_window (session_id, window_start, window_end, scheduled_at,
					       data_status, generation, checksum)
					VALUES (?, ?::timestamptz, ?::timestamptz, ?::timestamptz, 'VALID', ?, ?)""", sid,
					"2026-10-05T00:0" + i + ":00Z", "2026-10-05T00:0" + (i + 1) + ":00Z",
					"2026-10-05T00:0" + i + ":00Z", i == 1 ? 2 : 1, "c".repeat(64));
			jdbc.update("""
					INSERT INTO price_window_job (job_id, session_id, window_start, generation,
					       trigger_schema_version, status, attempt_count, delivery_expected)
					VALUES (?, ?, ?::timestamptz, 1, 't', 'SUCCEEDED', 1, true)""", "job-" + i, sid,
					"2026-10-05T00:0" + i + ":00Z");
		}
		// W0 의 세대 1 이력만 있다. W1 은 세대 2 로 정정됐고 세대 1 이력이 없다(도입 전 형상)
		jdbc.update("""
				INSERT INTO minute_window_artifact_commit (session_id, window_start, generation,
				       artifact_uri, artifact_checksum, manifest_uri, manifest_checksum)
				VALUES (?, '2026-10-05T00:00:00Z', 1, 's3://lake/w0g1', ?, 's3://lake/m', ?)""",
				sid, "a".repeat(64), "b".repeat(64));
		jdbc.update("""
				INSERT INTO minute_window_artifact_commit (session_id, window_start, generation,
				       artifact_uri, artifact_checksum, manifest_uri, manifest_checksum)
				VALUES (?, '2026-10-05T00:01:00Z', 2, 's3://lake/w1g2', ?, 's3://lake/m', ?)""",
				sid, "d".repeat(64), "b".repeat(64));
		jdbc.update("INSERT INTO minute_price_baseline_snapshot VALUES ('s1', ?, '500000', 'open_fallback', 'W0@g1', 100)", sid);
		jdbc.update("INSERT INTO minute_price_baseline_snapshot VALUES ('s2', ?, '500001', 'prev_close', '2026-10-02', 200)", sid);
		jdbc.update("INSERT INTO minute_price_baseline_set VALUES ('set1', '500000', 's1'), ('set1', '500001', 's2')");
		jdbc.update("""
				INSERT INTO minute_price_judgment (job_id, redrive_generation, attempt, session_id, window_start,
				       generation, detection_policy_version, abs_threshold, revert_threshold, baseline_set_id,
				       summary, anchors_used, tx_anchor, tx_anchor_locked)
				VALUES ('job-0', 0, 1, ?, '2026-10-05T00:00:00Z', 1, 'p', 0.03, 0.01, 'set1',
				        '{"fired": ["500000"], "inserted": ["500000"], "errors": []}', '{}',
				        '{"500000": null}', true)""", sid);

		List<PriceJudgmentRow> rows = repository.priceJudgments(sid);

		assertThat(rows).hasSize(2);
		PriceJudgmentRow w0 = rows.get(0), w1 = rows.get(1);
		assertThat(w0.attempt()).isEqualTo(1);
		assertThat(w0.artifactUri()).isEqualTo("s3://lake/w0g1");
		assertThat(w0.baselinesJson()).contains("500000").contains("open_fallback").doesNotContain("500001");
		assertThat(w0.judgedWithBaseline()).isEqualTo(2);
		assertThat(w0.baselineSetId()).isEqualTo("set1");
		// 집합 전체는 set 단위로 한 번 — 요약에 없는 500001 도 여기서 답한다
		assertThat(repository.priceBaselineSets(sid)).containsOnlyKeys("set1");
		assertThat(repository.priceBaselineSets(sid).get("set1")).contains("\"500001\"").contains("prev_close").contains("2026-10-02");
		assertThat(repository.priceBaselineSets("no-such-session")).isEmpty();
		assertThat(w0.txAnchorLocked()).isTrue();
		assertThat(w1.attempt()).isNull();                       // 기록 없음
		assertThat(w1.artifactChecksum()).isNull();              // 세대 1 이력 없음 — 세대 2 로 대체하지 않는다
		assertThat(w1.windowGeneration()).isEqualTo(2);
		assertThat(w1.jobGeneration()).isEqualTo(1);
	}
}
