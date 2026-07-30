package com.edge.tenantconsole.repository;

import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.AbstractPostgresIntegrationTest;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.service.ExplanationService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * explanations 사후 운영 쓰기(ALPHA-613)의 DB 계약을 실 Postgres 로 검증한다 — 손 대역이
 * 우회하는 실제 의미가 WHY(Rule 9): 원장 전이의 상태 가드(노출 중·차단·게시본 유무),
 * 두 테이블(analysis_item·publication) 동시 전이, status_history·console_action_log 감사,
 * 그리고 게시본 불일치 시 <b>같은 트랜잭션 전체 롤백</b>. 시드는 테스트 한정 JdbcTemplate,
 * id 는 it613- 접두, ticker 는 게시 grain((ticker,trade_date) PUBLISHED 유니크) 충돌을
 * 피해 케이스마다 다르게 준다. 실 writer 는 ExplanationService(콘솔 표면)다.
 */
class ExplanationWriteIT extends AbstractPostgresIntegrationTest {

	@Autowired
	private ExplanationService explanations;
	@Autowired
	private JdbcTemplate jdbc;

	private long cursor = 61300;

	// 컨테이너·데이터가 테스트 간 공유되므로(롤백 없음) email 은 전역 유니크여야 한다.
	private long seedMember() {
		return jdbc.queryForObject(
				"INSERT INTO member (email, name, role) "
						+ "VALUES (?, '검수자', 'COMPLIANCE_REVIEWER') RETURNING member_id",
				Long.class, "it613-" + java.util.UUID.randomUUID() + "@demo.edge.local");
	}

	private void seedItem(String id, String ticker, String status, String summary) {
		jdbc.update("""
				INSERT INTO analysis_item (explanation_result_id, etf_instrument_id, etf_ticker,
				    etf_name, trade_date, explanation_as_of, explanation_type, summary,
				    confidence_level, status, source_cursor, received_at)
				VALUES (?, 'i-613', ?, ?, '2026-07-15', now(), 'EVENT_SUPPORTED', ?, 'LOW', ?, ?, now())
				""", id, ticker, ticker, summary, status, cursor++);
	}

	private void seedPublication(String itemId, String ticker, String publishedSummary) {
		jdbc.update("INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, "
				+ "published_summary) VALUES (?, ?, '2026-07-15', ?)", itemId, ticker, publishedSummary);
	}

	private SessionMember actor(long memberId) {
		return new SessionMember(memberId, "it613@demo.edge.local", "검수자", "COMPLIANCE_REVIEWER");
	}

	private String status(String id) {
		return jdbc.queryForObject(
				"SELECT status FROM analysis_item WHERE explanation_result_id = ?", String.class, id);
	}

	private int countHistory(String id) {
		return jdbc.queryForObject(
				"SELECT count(*) FROM analysis_item_status_history WHERE analysis_item_id = ?",
				Integer.class, id);
	}

	private int countLog(String id) {
		return jdbc.queryForObject(
				"SELECT count(*) FROM console_action_log WHERE target_id = ?", Integer.class, id);
	}

	private Map<String, Object> lastLog(String id) {
		return jdbc.queryForMap("SELECT action, actor_id, target_type, target_id, "
				+ "detail::text AS detail, client_ip FROM console_action_log WHERE target_id = ? "
				+ "ORDER BY console_action_log_id DESC LIMIT 1", id);
	}

	private Map<String, Object> lastHistory(String id) {
		return jdbc.queryForMap("SELECT from_status, to_status, actor_type, actor_id, reason "
				+ "FROM analysis_item_status_history WHERE analysis_item_id = ? "
				+ "ORDER BY status_history_id DESC LIMIT 1", id);
	}

	/** 최신 감사 로그의 detail JSONB 에서 필드 하나를 뽑는다 — 전후값·사유의 필드 의미까지 단언하기 위함. */
	private String logDetailField(String id, String field) {
		return jdbc.queryForObject("SELECT detail ->> ? FROM console_action_log "
				+ "WHERE target_id = ? ORDER BY console_action_log_id DESC LIMIT 1",
				String.class, field, id);
	}

	// ── stop(제공 중단) ──

	@Test
	void stop_은_노출_건과_게시본을_함께_내리고_이력_감사를_남긴다() {
		long member = seedMember();
		seedItem("it613-stop", "613STOP", "AUTO_PUBLISHED", "원본");
		seedPublication("it613-stop", "613STOP", "게시 문구");

		explanations.stop("it613-stop", "이해상충 우려", actor(member), "10.0.0.1");

		// analysis_item·publication 이 한 트랜잭션에서 함께 UNPUBLISHED 로.
		assertThat(status("it613-stop")).isEqualTo("UNPUBLISHED");
		Map<String, Object> pub = jdbc.queryForMap("SELECT status, unpublish_reason, unpublished_by, "
				+ "unpublished_at FROM publication WHERE analysis_item_id = 'it613-stop'");
		assertThat(pub.get("status")).isEqualTo("UNPUBLISHED");
		assertThat(pub.get("unpublish_reason")).isEqualTo("이해상충 우려");
		assertThat(pub.get("unpublished_by")).isEqualTo(member);
		assertThat(pub.get("unpublished_at")).isNotNull();

		// MEMBER 전이가 이력 원장에 기록된다(읽은 상태를 from 으로).
		Map<String, Object> hist = lastHistory("it613-stop");
		assertThat(hist.get("from_status")).isEqualTo("AUTO_PUBLISHED");
		assertThat(hist.get("to_status")).isEqualTo("UNPUBLISHED");
		assertThat(hist.get("actor_type")).isEqualTo("MEMBER");
		assertThat(hist.get("actor_id")).isEqualTo(member);
		assertThat(hist.get("reason")).isEqualTo("이해상충 우려");

		// 감사 로그 — 행위자·사유·이전 상태·client IP. detail 은 필드 의미까지 단언한다
		// (문자열 contains 는 사유·상태가 엉뚱한 필드에 실려도 통과하므로 부족 — Rule 9).
		Map<String, Object> log = lastLog("it613-stop");
		assertThat(log.get("action")).isEqualTo("EXPLANATION_STOPPED");
		assertThat(log.get("actor_id")).isEqualTo(member);
		assertThat(log.get("client_ip")).isEqualTo("10.0.0.1");
		assertThat(logDetailField("it613-stop", "reason")).isEqualTo("이해상충 우려");
		assertThat(logDetailField("it613-stop", "fromStatus")).isEqualTo("AUTO_PUBLISHED");
	}

	@Test
	void stop_은_노출_중이_아니면_409다() {
		long member = seedMember();
		seedItem("it613-notserving", "613NSV", "REVIEW_REQUIRED", "원본");

		assertThatThrownBy(() -> explanations.stop("it613-notserving", "사유", actor(member), "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.NOT_SERVING));
	}

	@Test
	void stop_은_사유가_비면_원장_조회_전에_400이다() {
		long member = seedMember();

		assertThatThrownBy(() -> explanations.stop("it613-absent", "  ", actor(member), "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.REASON_REQUIRED));
	}

	@Test
	void stop_은_게시본이_없으면_409고_전이를_롤백한다() {
		long member = seedMember();
		seedItem("it613-nopub", "613NPB", "AUTO_PUBLISHED", "원본");   // 게시본 미시드

		assertThatThrownBy(() -> explanations.stop("it613-nopub", "사유", actor(member), "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.NOT_PUBLISHED));

		// WHY: publication 갱신이 0행이면 analysis_item 전이도 함께 롤백돼야 한다(같은
		// 트랜잭션) — 노출 중인데 게시본이 없는 불일치를 절반만 반영한 채 남기지 않는다.
		assertThat(status("it613-nopub")).isEqualTo("AUTO_PUBLISHED");
		assertThat(countHistory("it613-nopub")).isZero();
		assertThat(countLog("it613-nopub")).isZero();
	}

	// ── moveToReview(검수 이관) ──

	@Test
	void moveToReview_는_차단건을_검수대기로_되돌리고_이력_감사를_남긴다() {
		long member = seedMember();
		seedItem("it613-move", "613MOV", "BLOCKED", "원본");

		explanations.moveToReview("it613-move", actor(member), "10.0.0.2");

		assertThat(status("it613-move")).isEqualTo("REVIEW_REQUIRED");
		Map<String, Object> hist = lastHistory("it613-move");
		assertThat(hist.get("from_status")).isEqualTo("BLOCKED");
		assertThat(hist.get("to_status")).isEqualTo("REVIEW_REQUIRED");
		assertThat(hist.get("actor_type")).isEqualTo("MEMBER");
		assertThat(hist.get("actor_id")).isEqualTo(member);
		assertThat(hist.get("reason")).isNull();

		Map<String, Object> log = lastLog("it613-move");
		assertThat(log.get("action")).isEqualTo("EXPLANATION_MOVED_TO_REVIEW");
		assertThat(log.get("detail")).isNull();
	}

	@Test
	void moveToReview_는_차단이_아니면_409다() {
		long member = seedMember();
		seedItem("it613-notblocked", "613NBL", "AUTO_PUBLISHED", "원본");

		assertThatThrownBy(() -> explanations.moveToReview("it613-notblocked", actor(member), "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.NOT_BLOCKED));
	}

	// ── updateFinal(최종 문구 정정) ──

	@Test
	void updateFinal_은_게시본을_교체하고_전후값을_감사하되_이력은_남기지_않는다() {
		long member = seedMember();
		seedItem("it613-final", "613FIN", "APPROVED", "원본");
		seedPublication("it613-final", "613FIN", "이전 게시 문구");

		explanations.updateFinal("it613-final", "정정된 문구", actor(member), "10.0.0.3");

		assertThat(jdbc.queryForObject("SELECT published_summary FROM publication "
				+ "WHERE analysis_item_id = 'it613-final'", String.class)).isEqualTo("정정된 문구");
		// 상태 전이가 없으므로 이력 원장은 오염되지 않는다.
		assertThat(countHistory("it613-final")).isZero();

		// 전후값의 필드 의미까지 단언 — before·after 가 뒤바뀌거나 다른 필드에 실리면 잡는다.
		Map<String, Object> log = lastLog("it613-final");
		assertThat(log.get("action")).isEqualTo("EXPLANATION_FINAL_UPDATED");
		assertThat(log.get("actor_id")).isEqualTo(member);
		assertThat(logDetailField("it613-final", "before")).isEqualTo("이전 게시 문구");
		assertThat(logDetailField("it613-final", "after")).isEqualTo("정정된 문구");
	}

	@Test
	void updateFinal_은_자동게시_null_스냅샷이면_노출됐던_모델_원문을_before_로_남긴다() {
		long member = seedMember();
		seedItem("it613-nullsnap", "613NUL", "AUTO_PUBLISHED", "노출됐던 모델 원문");
		// 자동 게시본은 published_summary 가 NULL — 고객에게는 analysis_item.summary 가 노출된다.
		jdbc.update("INSERT INTO publication (analysis_item_id, etf_ticker, trade_date) "
				+ "VALUES ('it613-nullsnap', '613NUL', '2026-07-15')");

		explanations.updateFinal("it613-nullsnap", "검수자 정정 문구", actor(member), "10.0.0.4");

		assertThat(jdbc.queryForObject("SELECT published_summary FROM publication "
				+ "WHERE analysis_item_id = 'it613-nullsnap'", String.class)).isEqualTo("검수자 정정 문구");
		// WHY: 스냅샷 null 을 그대로 before=null 로 남기면 실제 노출됐던 원문을 감사에서
		// 잃는다 — before 는 노출됐던 문구(summary 폴백)여야 민원·감사가 재현된다.
		assertThat(logDetailField("it613-nullsnap", "before")).isEqualTo("노출됐던 모델 원문");
		assertThat(logDetailField("it613-nullsnap", "after")).isEqualTo("검수자 정정 문구");
	}

	@Test
	void updateFinal_은_게시본이_없으면_409다() {
		long member = seedMember();
		seedItem("it613-nofinal", "613NFN", "REVIEW_REQUIRED", "원본");   // 미게시(항목은 존재)

		assertThatThrownBy(() -> explanations.updateFinal("it613-nofinal", "문구", actor(member), "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.NOT_PUBLISHED));
	}

	@Test
	void updateFinal_은_없는_설명이면_404고_게시본_없음_409와_구분한다() {
		long member = seedMember();

		// 항목 자체가 없으면 게시본 없음(409)이 아니라 not-found(404) — 전이 3종 공통 계약.
		assertThatThrownBy(() -> explanations.updateFinal("it613-ghost", "문구", actor(member), "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.EXPLANATION_NOT_FOUND));
	}

	@Test
	void updateFinal_은_빈_문구면_원장_조회_전에_400이다() {
		long member = seedMember();

		assertThatThrownBy(() -> explanations.updateFinal("it613-absent", "", actor(member), "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.INVALID_REQUEST));
	}
}
