package com.edge.superadmin;

import com.edge.superadmin.repository.ConsoleFactsRepository;
import com.edge.superadmin.repository.ConsoleFactsRepository.IntradayAnalysisPoint;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.tuple;

/** 장중 분석 추이의 연속 날짜·코호트 도달·최신 실행 상태를 실 cloud 스키마로 검증한다. */
@Transactional
class JdbcIntradayAnalysisTrendRepositoryIntegrationTest extends CloudPostgresIntegrationTest {

	@Autowired
	private ConsoleFactsRepository repository;

	@Autowired
	private JdbcTemplate jdbc;

	private long triggerSeq;

	private void insertTrigger(String id, String day, String entity, String kind, String time) {
		jdbc.update("""
				INSERT INTO minute_ingestion_session (session_id, dataset, source_group,
				       session_date, universe_version, universe_hash, expected_window_count)
				VALUES (?, 'price_minute', 'toss', ?::date, 'u1', 'h1', 391)
				ON CONFLICT (dataset, source_group, session_date) DO NOTHING
				""", "session-" + day, day);
		jdbc.update("""
				INSERT INTO minute_price_trigger (trigger_id, entity_id, session_id, window_start,
				       generation, detection_policy_version, open_price, close_price, change_rate,
				       threshold, cooldown_bucket, trigger_kind)
				VALUES (?,?,?,?::timestamptz,1,'v2',1000,1030,0.03,0.03,?,?)
				""", id, entity, "session-" + day, day + "T" + time + "+09:00", triggerSeq++, kind);
	}

	/**
	 * 빈 날도 0 으로 내고, 트리거는 {@code FIRE} 만 KST 날짜로 센다 — 체인 입력({@code chain.feeds} 의 장중
	 * 수)과 같은 정의다(ALPHA-1330). 옛 설명 엔진의 단계(관측·실행·결과·게시)는 지웠다.
	 */
	@Test
	void 빈_날도_0으로_내고_FIRE_트리거만_KST_날짜로_오래된_날부터_센다() {
		insertTrigger("fire-1", "2026-08-02", "ETF-A", "FIRE", "09:31:00");
		insertTrigger("fire-2", "2026-08-02", "ETF-B", "FIRE", "10:31:00");
		insertTrigger("revert", "2026-08-02", "ETF-D", "REVERT", "12:31:00");
		/* 00:05 KST는 UTC 전날이다 — 존 변환이 빠지면 08-01로 새어 날짜별 수가 바뀐다. */
		insertTrigger("kst-boundary", "2026-08-02", "ETF-K", "FIRE", "00:05:00");
		insertTrigger("next-day", "2026-08-03", "ETF-E", "FIRE", "09:31:00");

		OffsetDateTime dbNow = jdbc.queryForObject("SELECT now()", OffsetDateTime.class);
		var trend = repository.intradayAnalysisTrend(LocalDate.parse("2026-08-03"), 3);

		/* 같은 트랜잭션의 now()는 고정이다 — 원장 시각이나 JVM 시각을 대신 내면 깨진다. */
		assertThat(trend.asOf()).isEqualTo(dbNow);
		assertThat(trend.points())
				.extracting(IntradayAnalysisPoint::date, IntradayAnalysisPoint::triggers)
				.containsExactly(
						tuple(LocalDate.parse("2026-08-01"), 0L),
						tuple(LocalDate.parse("2026-08-02"), 3L),
						tuple(LocalDate.parse("2026-08-03"), 1L));
		// 체인 입력과 같은 정의다 — 같은 날짜면 같은 수다
		assertThat(trend.points().get(1).triggers())
				.isEqualTo(repository.facts(LocalDate.parse("2026-08-02")).chain().feeds().get(1).v());
	}
}
