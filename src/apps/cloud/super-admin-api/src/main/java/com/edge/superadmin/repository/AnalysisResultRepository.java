package com.edge.superadmin.repository;

import tools.jackson.databind.JsonNode;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;

/**
 * 새 분석 엔진(v2)의 결과 행을 한 건씩 보여 주는 읽기 전용 조회(ALPHA-1333). 표는
 * {@code outlook_*}·{@code movement_*} 다 — 옛 설명 엔진 표({@code explanation_*})를 읽는
 * {@link AnalysisRepository} 와 별개다.
 *
 * <p><b>게시본·회수·합성 입력의 기준은 v2 테넌트 조회와 같다</b>
 * ({@code analysis-engine-v2 api/publications.py} {@code PublicationReader.latest}):
 * 게시본 = {@code status='completed' AND data_source='database' AND published_at IS NOT NULL},
 * 가격변동은 여기에 {@code withdrawn_at IS NULL} 이 더해진다. 전망에는 회수 열이 없다.
 *
 * <p>상태는 행의 {@code status} 그대로다(completed·failed·running) — 새로 판정하지 않는다.
 * 분석 내용도 저장된 그대로 낸다. 목록의 제목·오류 앞부분만 자른다.
 */
public interface AnalysisResultRepository {

	enum Kind { OUTLOOK, MOVEMENT }

	/**
	 * @param from            {@code analysis_at} 하한(포함)
	 * @param to              {@code analysis_at} 상한(제외)
	 * @param etf             ETF 코드. null 이면 전부
	 * @param status          행의 status. null 이면 전부
	 * @param allSources      false 면 {@code data_source='database'} 만(테넌트 조회와 같다). true 면
	 *                        합성 입력({@code synthetic})과 출처 표기 전의 행({@code unknown})도 함께
	 */
	record ListQuery(Kind kind, OffsetDateTime from, OffsetDateTime to, String etf, String status,
			boolean allSources, int page, int size) {
	}

	/** 조건에 맞는 전체 행 수와 이번 페이지의 행. 행은 분석 시각 최신순이다. */
	record Page(long total, int page, int size, List<ListRow> rows) {
	}

	/**
	 * @param published 테넌트 조회가 이 분석을 게시본으로 내는가(클래스 주석의 기준)
	 * @param withdrawn 가격변동만 값이 있다. 전망은 회수 열이 없어 null
	 * @param title     전망은 요약 제목(없으면 요약 첫 줄), 가격변동은 요약 첫 줄 — 앞부분만
	 * @param errorHead 실패 행의 오류 메시지 앞부분
	 */
	record ListRow(String analysisId, String etfCode, String etfName, OffsetDateTime analysisAt,
			String status, String dataSource, boolean published, OffsetDateTime publishedAt,
			Boolean withdrawn, OffsetDateTime withdrawnAt, String title, String errorHead) {
	}

	Page list(ListQuery query);

	/** 종류마다 한 갈래만 채운다 — 전망이면 {@code outlook}, 가격변동이면 {@code movement}. */
	record Detail(String analysisId, String kind, String etfCode, String etfName,
			OffsetDateTime analysisAt, String status, String dataSource, boolean published,
			OffsetDateTime publishedAt, String previousAnalysisId, String errorMessage,
			Outlook outlook, Movement movement) {
	}

	record Outlook(String sticker, String summaryTitle, String summary, String detailTitle,
			String detailMode, String conclusionTitle, String conclusionSentence,
			String changeCondition, List<OutlookItem> items, List<OutlookFactor> factors,
			List<ConclusionKeyword> conclusionKeywords) {
	}

	/** {@code outlook_items} 한 행 — section(detail·update)·position 순이다. */
	record OutlookItem(String itemId, String section, String changeType, int position,
			String titleKeyword, JsonNode bullets, String sentence, String sentiment,
			JsonNode sourceLinks, OffsetDateTime sourceAsOf, List<String> toolRunIds) {
	}

	record OutlookFactor(String type, String sticker, String sentence) {
	}

	record ConclusionKeyword(String kind, int position, String label, List<String> toolRunIds) {
	}

	/**
	 * @param selectedItemIds 고른 항목의 id — 저장된 순서 그대로
	 * @param items           이 분석의 {@code movement_items} 전부(고르지 않은 항목도 든다)
	 */
	record Movement(LocalDate tradingDate, String summary, List<String> selectedItemIds,
			boolean withdrawn, OffsetDateTime withdrawnAt, List<MovementItem> items) {
	}

	record MovementItem(String itemId, String type, String titleKeyword, String sentence,
			String sentiment, OffsetDateTime sourceAsOf, List<String> toolRunIds) {
	}

	/** @return 그런 분석이 없으면 null */
	Detail detail(Kind kind, String analysisId);
}
