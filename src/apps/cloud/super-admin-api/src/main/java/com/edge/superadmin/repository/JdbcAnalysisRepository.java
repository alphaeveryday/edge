package com.edge.superadmin.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * {@link AnalysisRepository} 의 JdbcTemplate 구현 — 런 목록과 문서 근거를 두 조회로 읽어
 * 조립한다(ALPHA-601, 선례 {@link JdbcPipelineStatusRepository}).
 *
 * <p><b>조인 하나로 합치지 않는 이유</b>: 근거는 런당 여러 개라 한 SELECT 로 붙이면 런 행이
 * 근거 수만큼 불어나고, 중복을 코드에서 다시 걷어내야 한다(드릴다운과 같은 구조).
 *
 * <p>두 조회는 REPEATABLE READ 한 스냅샷 안에서 돈다 — 사이에 writer 가 커밋하면 "목록엔
 * 있는데 근거가 빈" 어느 시점에도 존재하지 않은 조합이 조립된다.
 */
@Repository
public class JdbcAnalysisRepository implements AnalysisRepository {

	/**
	 * 목록 창 상한 — 원장은 무기한 보존이라 무제한 스캔을 막는다. 런은 트리거(ETF×거래일)당
	 * 1개 수준(현행 유니버스 31종)이라 200 이면 여러 거래일이 담긴다.
	 */
	// ponytail: 고정 LIMIT — 기간 파라미터·페이지네이션은 화면 요구가 생길 때
	private static final int LIST_LIMIT = 200;

	/**
	 * 런당 근거 표시 상한. 한 설명이 프롬프트에 싣는 사건 수가 수십~수백 건이라(dev 실측 평균
	 * 56 · 최대 485) 상한이 없으면 목록 응답 하나가 {@code LIST_LIMIT} 배로 부푼다. 잘라낸
	 * 만큼은 {@code evidenceTotal} 이 말해 준다 — 화면의 건수가 표시 건수로 축소되면 근거가
	 * 그것뿐인 것처럼 읽힌다.
	 */
	private static final int EVIDENCE_LIMIT_PER_RUN = 20;

	/**
	 * 트리거→기여관찰→경로→런이 전부 1:1 체인이라(각 FK 에 UNIQUE) 행이 불어나지 않는다.
	 * {@code explanation_result} 만 LEFT — 결과가 아직 없는 런도 목록에 남아야 한다.
	 *
	 * <p>트리거 축은 둘이다(ALPHA-709) — 일 단위({@code price_movement_trigger})와 분봉
	 * ({@code minute_price_trigger}). 관측 한 행은 정확히 한 축만 갖는다(one-of CHECK)라
	 * 두 축을 LEFT 로 걸고 COALESCE 한다 — 일 단위만 INNER 로 걸면 분봉 계보의 런이
	 * 목록에서 통째로 사라진다. 분봉의 observed_return 은 부호 있는 close/open−1 재구성
	 * (행의 change_rate 는 절대값), instrument 는 entity_id(단축코드)→ticker 로 잇되
	 * KR MIC(XKRX·XKOS·XKON)로 좁힌다 — instrument 유일성이 (market_code, ticker)라
	 * 타 시장 동일 ticker 가 있으면 한 런이 두 행으로 불어난다(1분 트랙은 KR 전용이고
	 * 6자리 단축코드는 KR 안에서 전국 유일이다).
	 *
	 * <p>동률 해소를 명시한다({@code explanation_run_id}) — {@code explanation_as_of} 에 유일성
	 * 제약이 없어 동률이면 페이지 경계 행이 조회마다 달라진다.
	 */
	private static final String LIST_SQL = """
			SELECT er.explanation_run_id, er.run_status, er.finished_at,
			       COALESCE(tr.observed_return, mt.close_price / mt.open_price - 1)
			           AS observed_return,
			       COALESCE(tr.detected_at, mt.created_at) AS detected_at,
			       e.display_name, i.ticker, i.market_code,
			       res.summary, res.confidence_level
			  FROM explanation_run er
			  JOIN explanation_route rt ON rt.explanation_route_id = er.explanation_route_id
			  JOIN etf_contribution_observation co
			         ON co.contribution_observation_id = rt.contribution_observation_id
			  LEFT JOIN price_movement_trigger tr
			         ON tr.price_movement_trigger_id = co.price_movement_trigger_id
			  LEFT JOIN minute_price_trigger mt
			         ON mt.trigger_id = co.minute_price_trigger_id
			  JOIN instrument i
			         ON (tr.etf_instrument_id IS NOT NULL
			                 AND i.instrument_id = tr.etf_instrument_id)
			             OR (mt.trigger_id IS NOT NULL
			                 AND i.ticker = mt.entity_id AND i.instrument_type = 'ETF'
			                 AND i.market_code IN ('XKRX', 'XKOS', 'XKON'))
			  JOIN entity e ON e.entity_id = i.instrument_id
			  LEFT JOIN explanation_result res ON res.explanation_run_id = er.explanation_run_id
			 ORDER BY er.explanation_as_of DESC, er.explanation_run_id DESC
			 LIMIT %d
			""".formatted(LIST_LIMIT);

	/**
	 * 문서 실체가 있는 lineage 두 갈래를 합친다 — 이벤트 근거(뉴스·공시 주장)와 공시 정규화
	 * 사실({@code explanation_run_disclosure_fact}). 가격 관찰 lineage
	 * ({@code explanation_run_event_price_observation})는 제목·출처 문안이 없는 수치 관찰이라
	 * 여기서 문서처럼 그리지 않는다(문안을 지어내지 않는다 — 표시 계약이 생기면 별도 편입).
	 *
	 * <p>DISTINCT — 같은 문서가 여러 주장·여러 단계(stage_code)·두 lineage 로 한 런에 연결될
	 * 수 있는데, 운영자에게 근거는 문서 단위다. 서브쿼리는 LIST_SQL 과 같은 창이라 한 스냅샷
	 * 안에서 같은 런 집합을 본다. {@code document_id} 는 정렬 동률 해소용으로만 SELECT 에
	 * 남는다(published_at 은 NULL 허용·비유일).
	 *
	 * <p>런당 {@code EVIDENCE_LIMIT_PER_RUN} 건으로 자르되 {@code evidence_total} 을 같이 낸다
	 * — 자른 사실을 표시 층이 알아야 "N건" 이 표시 건수로 둔갑하지 않는다. 상한은 DISTINCT
	 * <b>뒤</b>에 걸린다(문서 단위로 20건이지 lineage 행 20건이 아니다).
	 */
	private static final String EVIDENCE_SQL = """
			SELECT explanation_run_id, document_type, title, source_code, published_at,
			       evidence_total
			  FROM (
			       SELECT explanation_run_id, document_id, document_type, title,
			              source_code, published_at,
			              ROW_NUMBER() OVER (PARTITION BY explanation_run_id
			                                 ORDER BY published_at ASC NULLS LAST, document_id)
			                  AS document_rank,
			              COUNT(*) OVER (PARTITION BY explanation_run_id) AS evidence_total
			         FROM (
			              SELECT DISTINCT explanation_run_id, document_id, document_type, title,
			                     source_code, published_at
			                FROM (
			                   SELECT ree.explanation_run_id, d.document_id, d.document_type,
			                          d.title, d.source_code, d.published_at
			                     FROM explanation_run_event_evidence ree
			                     JOIN event_evidence ev ON ev.evidence_id = ree.evidence_id
			                     JOIN document_assertion da ON da.assertion_id = ev.assertion_id
			                     JOIN document d ON d.document_id = da.document_id
			                   UNION ALL
			                   SELECT rdf.explanation_run_id, d.document_id, d.document_type,
			                          d.title, d.source_code, d.published_at
			                     FROM explanation_run_disclosure_fact rdf
			                     JOIN disclosure_fact df ON df.fact_id = rdf.fact_id
			                     JOIN document d ON d.document_id = df.document_id
			                   ) lineage
			               WHERE explanation_run_id IN (
			                     SELECT explanation_run_id FROM explanation_run
			                      ORDER BY explanation_as_of DESC, explanation_run_id DESC
			                      LIMIT %d)
			              ) documents
			       ) ranked
			 WHERE document_rank <= %d
			 ORDER BY explanation_run_id, document_rank
			""".formatted(LIST_LIMIT, EVIDENCE_LIMIT_PER_RUN);

	/**
	 * 운영자 작업 오버레이 — 창 안의 런별 최신 액션을 admin_activity_log 에서 유도한다(ALPHA-602).
	 * explanation_result(파이프라인 소유)를 덮지 않는다: 정정 본문·제외 여부를 이 원장에서 읽어
	 * 표시 층이 덧입힌다. 복원은 EXCLUDE 를 취소해 원상태(run_status)를 그대로 되살린다.
	 *
	 * <p>{@code excluded} = EXCLUDE/RESTORE 중 최신이 EXCLUDE. {@code corrected} = 정정 이력 존재.
	 * {@code corrected_summary} = 최신 정정본(details.after). FILTER 로 액션별 집계를 분리한다 —
	 * 해당 액션이 없으면 array 가 비어 [1] 이 NULL(제외 아님 / 정정본 없음).
	 *
	 * <p>창(LIMIT)은 LIST_SQL 과 같은 정렬의 explanation_run 상위 200 이다 — LIST_SQL 조인은 전부
	 * 필수 1:1 체인(route→기여관찰→트리거→instrument→entity, 전부 NOT NULL)이라 런을 떨구지 않아
	 * 두 창의 런 집합이 일치한다(EVIDENCE_SQL 과 같은 불변식).
	 */
	private static final String OVERLAY_SQL = """
			SELECT target_id,
			       bool_or(action = 'ANALYSIS_RESULT_CORRECTED') AS corrected,
			       (array_agg(action ORDER BY activity_id DESC)
			          FILTER (WHERE action IN ('ANALYSIS_EXCLUDED', 'ANALYSIS_RESTORED')))[1]
			          = 'ANALYSIS_EXCLUDED' AS excluded,
			       (array_agg(details ->> 'after' ORDER BY activity_id DESC)
			          FILTER (WHERE action = 'ANALYSIS_RESULT_CORRECTED'))[1] AS corrected_summary
			  FROM admin_activity_log
			 WHERE target_type = 'ANALYSIS_RUN'
			   AND target_id IN (
			       SELECT explanation_run_id FROM explanation_run
			        ORDER BY explanation_as_of DESC, explanation_run_id DESC
			        LIMIT %d)
			 GROUP BY target_id
			""".formatted(LIST_LIMIT);

	private final JdbcTemplate jdbc;

	public JdbcAnalysisRepository(JdbcTemplate jdbc) {
		this.jdbc = jdbc;
	}

	private record Overlay(boolean excluded, boolean corrected, String correctedSummary) {
	}

	@Override
	@Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
	public List<AnalysisRow> list() {
		Map<String, List<EvidenceRow>> evidenceByRun = new HashMap<>();
		Map<String, Integer> totalByRun = new HashMap<>();
		jdbc.query(EVIDENCE_SQL, rs -> {
			String runId = rs.getString("explanation_run_id");
			evidenceByRun.computeIfAbsent(runId, k -> new ArrayList<>())
					.add(new EvidenceRow(
							rs.getString("document_type"),
							rs.getString("title"),
							rs.getString("source_code"),
							rs.getObject("published_at", OffsetDateTime.class)));
			totalByRun.put(runId, rs.getInt("evidence_total"));
		});
		Map<String, Overlay> overlayByRun = new HashMap<>();
		jdbc.query(OVERLAY_SQL, rs -> {
			overlayByRun.put(rs.getString("target_id"), new Overlay(
					rs.getBoolean("excluded"), rs.getBoolean("corrected"),
					rs.getString("corrected_summary")));
		});
		return jdbc.query(LIST_SQL, (rs, i) -> {
			String runId = rs.getString("explanation_run_id");
			Overlay overlay = overlayByRun.getOrDefault(runId, NO_OVERLAY);
			return new AnalysisRow(
					runId,
					rs.getString("display_name"),
					rs.getString("ticker"),
					rs.getString("market_code"),
					rs.getDouble("observed_return"),
					rs.getString("run_status"),
					rs.getObject("detected_at", OffsetDateTime.class),
					rs.getObject("finished_at", OffsetDateTime.class),
					rs.getString("summary"),
					rs.getString("confidence_level"),
					overlay.excluded(),
					overlay.corrected(),
					overlay.correctedSummary(),
					List.copyOf(evidenceByRun.getOrDefault(runId, List.of())),
					totalByRun.getOrDefault(runId, 0));
		});

	}

	private static final Overlay NO_OVERLAY = new Overlay(false, false, null);
}
