package com.edge.intake;

import com.edge.intake.repository.ReceivedBundleRepository;
import com.edge.intake.repository.SyncStateRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;

import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * intake 적재 리포지토리 계약을 실 Postgres 로 검증한다(sync-protocol.md 전달 보장):
 * ① received_bundle 는 cursor_from 기준 dedup(ON CONFLICT) — 재수신 무해, 원본 불변
 * ② sync_state committed cursor 는 durable 하게 단조 전진한다.
 * @Immutable 은 아니지만 쓰기가 native @Query 라 시드/검증은 JdbcTemplate 으로 직접 한다.
 */
class IntakeRepositoryIntegrationTest extends OnpremPostgresIntegrationTest {

	// received_bundle CHECK(checksum ~ '^sha256=[0-9a-f]{64}$') 를 만족하는 형식.
	private static final String CHECKSUM = "sha256=" + "a".repeat(64);

	@Autowired
	private ReceivedBundleRepository receivedBundles;

	@Autowired
	private SyncStateRepository syncState;

	@Autowired
	private JdbcTemplate jdbc;

	@BeforeEach
	void clean() {
		jdbc.update("DELETE FROM received_bundle");
		// baseline 이 심은 단일 행을 재개점 0 으로 되돌린다(테스트 격리).
		jdbc.update("UPDATE sync_state SET last_cursor = 0, last_synced_at = NULL");
	}

	@Test
	void save_는_신규_cursor를_저장하고_중복_cursor는_원본을_지킨다() {
		// WHY: at-least-once 재-Pull 은 같은 cursor_from 을 다시 보낼 수 있다 — dedup 이 없으면
		// 원본 바이트가 덮여 감사 추적이 깨진다(ON CONFLICT DO NOTHING 으로 최초분 보존).
		assertThat(receivedBundles.save(1, 3, CHECKSUM, "first".getBytes(StandardCharsets.UTF_8)))
				.isEqualTo(1);
		assertThat(receivedBundles.save(1, 9, CHECKSUM, "second".getBytes(StandardCharsets.UTF_8)))
				.isEqualTo(0); // 같은 cursor_from → skip

		Long rows = jdbc.queryForObject("SELECT count(*) FROM received_bundle WHERE cursor_from = 1", Long.class);
		assertThat(rows).isEqualTo(1L);
		// 최초 저장분(cursor_to=3, body=first)이 그대로 남아야 한다.
		Long cursorTo = jdbc.queryForObject("SELECT cursor_to FROM received_bundle WHERE cursor_from = 1", Long.class);
		assertThat(cursorTo).isEqualTo(3L);
		byte[] body = jdbc.queryForObject("SELECT body FROM received_bundle WHERE cursor_from = 1", byte[].class);
		assertThat(new String(body, StandardCharsets.UTF_8)).isEqualTo("first");
	}

	@Test
	void relax_후_checksum_없이도_저장된다() {
		// WHY: ADR-0040 확장 마이그레이션(V202607271500)이 received_bundle.checksum 의 NOT NULL·
		// CHECK(^sha256=) 를 풀었다 — cloud 봉투 전환(T2) 후 X-Bundle-Checksum 헤더가 사라져 checksum
		// 이 null 로 들어와도 INSERT 가 깨지면 안 된다. 이 제약이 살아 있으면 T2 배포가 전 번들을 거부한다.
		int rows = jdbc.update(
				"INSERT INTO received_bundle (cursor_from, cursor_to, body) VALUES (?, ?, ?)",
				10L, 12L, "no-checksum".getBytes(StandardCharsets.UTF_8));
		assertThat(rows).isEqualTo(1);

		String checksum = jdbc.queryForObject(
				"SELECT checksum FROM received_bundle WHERE cursor_from = 10", String.class);
		assertThat(checksum).isNull();
	}

	@Test
	void lastCursor_advance_는_committed_커서를_durable하게_전진시킨다() {
		// WHY: committed cursor 는 권위 재개점(sync-protocol.md) — 이 값이 durable 하지 않으면
		// 재기동 후 소비 완료 번들을 재수신하거나 유실한다.
		assertThat(syncState.lastCursor()).isEqualTo(0L); // baseline 단일 행

		syncState.advance(7);

		assertThat(syncState.lastCursor()).isEqualTo(7L);
	}
}
