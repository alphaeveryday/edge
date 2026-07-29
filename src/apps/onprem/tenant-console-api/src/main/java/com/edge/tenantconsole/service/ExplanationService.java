package com.edge.tenantconsole.service;

import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.entity.AnalysisItemEntity;
import com.edge.tenantconsole.entity.PublicationEntity;
import com.edge.tenantconsole.entity.ScreeningCheckEntity;
import com.edge.tenantconsole.entity.ScreeningRuleEntity;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.mock.ExplanationMockStore;
import com.edge.tenantconsole.model.Explanation;
import com.edge.tenantconsole.model.FeedStatus;
import com.edge.tenantconsole.model.FeedStatusAggregate;
import com.edge.tenantconsole.repository.ExplanationLedgerRepository;
import com.edge.tenantconsole.repository.PublishedSummaryRepository;
import com.edge.tenantconsole.repository.ScreeningCheckRepository;
import com.edge.tenantconsole.repository.ScreeningRuleRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.Duration;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.LongPredicate;

/**
 * 가격 변동 설명 표면 — 읽기(목록·반입 상태)는 온프렘 원장 실조회(ALPHA-607), 쓰기
 * (최종 문구·이관·중단·승인·반려·임시저장)는 아직 mock 스토어다. 원장의 설명 ID
 * (explanation_result_id)는 mock 에 없으므로 <b>실목록에서 고른 건의 쓰기는 전부
 * 404 로 실패한다</b> — 조용히 성공한 척하는 것보다 낫고, 원장 전이·행위자·감사와 함께
 * 쓰기 전환(후속 티켓, ALPHA-497 materialization 후)이 이 자리를 교체한다.
 *
 * <p>매핑(축소 계약, 사용자 결정 2026-07-29): name←etf_name, code←etf_ticker, status,
 * risk←confidence_level, reviewReason←screening_check(REVIEW·BLOCK)→rule_type 파생,
 * receivedAt←received_at, evidence←evidences JSONB, original←summary,
 * final←publication.published_summary(널이면 summary). market·direction·changePct 는
 * 온프렘 원장에 없어(ALPHA-497 이연) 제거됐다.
 */
@Service
public class ExplanationService {

	private static final Logger log = LoggerFactory.getLogger(ExplanationService.class);

	private static final ZoneId KST = ZoneId.of("Asia/Seoul");

	/** 화면 노출 상태(labels.ts ServeStatus 6종) — 수신 전(RECEIVED)·정정 리비전(CORRECTED·INVALIDATED)은 제외. */
	private static final List<String> VISIBLE_STATUSES = List.of(
			"AUTO_PUBLISHED", "APPROVED", "REVIEW_REQUIRED", "BLOCKED", "REJECTED", "UNPUBLISHED");

	/** 사유가 붙는 분기 — 검수 대기·반려는 REVIEW 판정, 차단은 BLOCK 판정에서 파생한다. */
	private static final List<String> REASON_RESULTS = List.of("REVIEW", "BLOCK");

	/** 사유를 노출하는 상태 — 승인·자동 제공·중단은 과거 REVIEW 검사가 남아도 사유가 없다(승인 시 해소). */
	private static final Set<String> REASON_STATUSES = Set.of("REVIEW_REQUIRED", "BLOCKED", "REJECTED");

	/**
	 * 반입 상태 판정 임계 — 데모 휴리스틱(피드 SLA 미정, 후속 확정 대상). 최근 반입이
	 * FRESH 이내면 정상, STALE 이내면 지연, 그 밖(또는 반입 이력 없음)은 중단으로 본다.
	 */
	private static final Duration FEED_FRESH = Duration.ofHours(3);
	private static final Duration FEED_STALE = Duration.ofHours(24);

	private final ExplanationLedgerRepository ledger;
	private final ScreeningCheckRepository screeningCheckRepository;
	private final ScreeningRuleRepository screeningRuleRepository;
	private final PublishedSummaryRepository publishedSummaryRepository;
	private final ExplanationMockStore store;
	private final ObjectMapper objectMapper = new ObjectMapper();

	public ExplanationService(ExplanationLedgerRepository ledger,
			ScreeningCheckRepository screeningCheckRepository,
			ScreeningRuleRepository screeningRuleRepository,
			PublishedSummaryRepository publishedSummaryRepository,
			ExplanationMockStore store) {
		this.ledger = ledger;
		this.screeningCheckRepository = screeningCheckRepository;
		this.screeningRuleRepository = screeningRuleRepository;
		this.publishedSummaryRepository = publishedSummaryRepository;
		this.store = store;
	}

	public List<Explanation> list() {
		List<AnalysisItemEntity> items = ledger.findByStatusInOrderByReceivedAtDesc(VISIBLE_STATUSES);
		List<String> ids = items.stream().map(AnalysisItemEntity::getExplanationResultId).toList();
		Map<String, String> reasons = reviewReasonsFor(items);
		Map<String, String> finals = publishedSummariesFor(ids);
		return items.stream().map(it -> new Explanation(
				it.getExplanationResultId(),
				displayName(it),
				displayCode(it),
				it.getStatus(),
				it.getConfidenceLevel(),
				reasons.get(it.getExplanationResultId()),
				it.getReceivedAt(),
				parseEvidence(it.getEvidences()),
				it.getSummary(),
				finals.getOrDefault(it.getExplanationResultId(), it.getSummary()))
		).toList();
	}

	// etf_name·etf_ticker 는 원장에서 nullable(미게시 항목은 ticker 결측 가능) — UI 계약
	// (name·code: string)을 지키고 목록 검색(toLowerCase)이 NPE 나지 않게 표시 폴백을 준다.
	private static String displayName(AnalysisItemEntity it) {
		if (it.getEtfName() != null && !it.getEtfName().isBlank()) {
			return it.getEtfName();
		}
		return it.getEtfTicker() != null && !it.getEtfTicker().isBlank()
				? it.getEtfTicker() : "(이름 없음)";
	}

	private static String displayCode(AnalysisItemEntity it) {
		return it.getEtfTicker() != null && !it.getEtfTicker().isBlank() ? it.getEtfTicker() : "—";
	}

	public FeedStatus feedStatus() {
		FeedStatusAggregate agg = ledger.summarizeFeed(startOfTodayKst());
		OffsetDateTime last = agg == null ? null : agg.lastReceivedAt();
		long today = agg == null ? 0 : agg.todayReceived();
		return new FeedStatus(feedState(last), last, today);
	}

	// ── 검수 사유 파생: screening_check(REVIEW|BLOCK) → screening_rule.rule_type → UI 사유 ──

	/**
	 * 항목별 사유 — UI 계약(reviewReason?: 단일)에 맞춰 항목당 1건. 사유는 <b>현재 상태의
	 * 분기 판정</b>에서 취한다: 차단(BLOCKED)은 result='BLOCK' 행, 그 외(검수 대기·반려)는
	 * result='REVIEW' 행. 상태를 안 보고 첫 check 를 취하면 REVIEW→BLOCK 순서로 기록된
	 * 차단 항목이 실제 차단 원인 대신 이관 사유를 노출한다(사유 파생 규약과도 어긋남).
	 */
	private Map<String, String> reviewReasonsFor(List<AnalysisItemEntity> items) {
		// 사유 대상은 검수 대기·차단·반려 상태뿐 — 승인·자동 제공·중단 항목은 과거 REVIEW
		// 검사 행이 남아 있어도 사유를 노출하지 않는다(승인 시 사유 해소, 기존 mock 불변식).
		Map<String, String> statusById = new HashMap<>();
		for (AnalysisItemEntity it : items) {
			if (REASON_STATUSES.contains(it.getStatus())) {
				statusById.put(it.getExplanationResultId(), it.getStatus());
			}
		}
		if (statusById.isEmpty()) {
			return Map.of();
		}
		List<ScreeningCheckEntity> checks = screeningCheckRepository
				.findByAnalysisItemIdInAndResultInOrderByScreeningCheckId(statusById.keySet(), REASON_RESULTS);
		Map<Long, String> ruleTypes = ruleTypesById(checks.stream()
				.map(ScreeningCheckEntity::getScreeningRuleId).filter(id -> id != null).toList());
		Map<String, String> firstReason = new LinkedHashMap<>();
		for (ScreeningCheckEntity check : checks) {
			String itemId = check.getAnalysisItemId();
			// 룰 무관 판정(자동 제공 스위치 등)은 UI 사유 어휘가 아니고, 상태와 무관한
			// 분기(BLOCKED 는 BLOCK, 그 외는 REVIEW)는 사유의 원인이 아니라 건너뛴다.
			if (check.getScreeningRuleId() == null || firstReason.containsKey(itemId)
					|| !wantedResult(statusById.get(itemId)).equals(check.getResult())) {
				continue;
			}
			String reason = uiReason(ruleTypes.get(check.getScreeningRuleId()));
			if (reason != null) {
				firstReason.put(itemId, reason);
			}
		}
		return firstReason;
	}

	private static String wantedResult(String status) {
		return "BLOCKED".equals(status) ? "BLOCK" : "REVIEW";
	}

	private Map<Long, String> ruleTypesById(Collection<Long> ruleIds) {
		if (ruleIds.isEmpty()) {
			return Map.of();
		}
		Map<Long, String> types = new HashMap<>();
		for (ScreeningRuleEntity rule : screeningRuleRepository.findByScreeningRuleIdIn(ruleIds)) {
			types.put(rule.getScreeningRuleId(), rule.getRuleType());
		}
		return types;
	}

	/** rule_type(스키마 어휘) → UI ReviewReason. ASSERTIVE_EXPRESSION 만 어휘가 다르다. 미지 값은 그대로 노출(Rule 12). */
	private static String uiReason(String ruleType) {
		if (ruleType == null) {
			return null;
		}
		return switch (ruleType) {
			case "ASSERTIVE_EXPRESSION" -> "ASSERTIVE";
			default -> ruleType;
		};
	}

	// ── 최종 문구: publication.published_summary(PUBLISHED) 배치 조회 ──

	private Map<String, String> publishedSummariesFor(Collection<String> ids) {
		if (ids.isEmpty()) {
			return Map.of();
		}
		// 항목당 최신 게시본(publication_id 최대)을 고른다 — 재게시 이력이 있으면 마지막
		// 게시 문구가, 중단(UNPUBLISHED)된 항목이면 마지막으로 노출됐던 문구가 final 이 된다.
		Map<String, PublicationEntity> latest = new HashMap<>();
		for (PublicationEntity pub : publishedSummaryRepository.findByAnalysisItemIdIn(ids)) {
			latest.merge(pub.getAnalysisItemId(), pub,
					(a, b) -> b.getPublicationId() > a.getPublicationId() ? b : a);
		}
		Map<String, String> byItem = new HashMap<>();
		latest.forEach((id, pub) -> {
			if (pub.getPublishedSummary() != null) {
				byItem.put(id, pub.getPublishedSummary());
			}
		});
		return byItem;
	}

	// ── evidences JSONB([{kind,title,source,published_at}]) → 도메인 근거 목록 ──

	private List<Explanation.Evidence> parseEvidence(String evidencesJson) {
		if (evidencesJson == null || evidencesJson.isBlank()) {
			return List.of();
		}
		// 근거 결측은 항목 단위로 격리한다 — 한 항목의 깨진 evidences 가 목록 전체(GET
		// /explanations)를 500 으로 무너뜨리지 않게. 결측은 조용히 빈 값이 아니라 로그로 드러낸다(Rule 12).
		JsonNode array;
		try {
			array = objectMapper.readTree(evidencesJson);
		} catch (RuntimeException e) {
			log.warn("evidences JSON 파싱 실패 — 근거 생략: {}", e.getMessage());
			return List.of();
		}
		if (!array.isArray()) {
			log.warn("evidences 가 배열이 아님({}) — 근거 생략", array.getNodeType());
			return List.of();
		}
		List<Explanation.Evidence> out = new ArrayList<>();
		for (JsonNode node : array) {
			// 배열 안의 불량 요소(null·스칼라·kind 결측)는 kind/source 가 null 인 반쪽 근거로
			// 직렬화되어 UI 계약(type: 공시|뉴스, source: string)을 깨므로 로그로 드러내고 건너뛴다.
			if (!node.isObject()) {
				log.warn("근거 요소가 객체가 아님({}) — 생략", node.getNodeType());
				continue;
			}
			String kind = text(node, "kind");
			if (kind == null || kind.isBlank()) {
				log.warn("근거 kind 결측·비문자열 — 생략");
				continue;
			}
			out.add(new Explanation.Evidence(
					kind, text(node, "title"), text(node, "source"), parseTime(node)));
		}
		return out;
	}

	/** published_at 은 계약상 ISO offset date-time 이나, 불량 값에 목록이 무너지지 않게 시각만 생략한다. */
	private static OffsetDateTime parseTime(JsonNode node) {
		if (!node.hasNonNull("published_at")) {
			return null;
		}
		String raw = node.get("published_at").asString();
		try {
			return OffsetDateTime.parse(raw);
		} catch (RuntimeException e) {
			log.warn("근거 published_at 파싱 실패 — 시각 생략: {}", raw);
			return null;
		}
	}

	// 문자열 노드만 값으로 인정한다 — kind/title/source 가 객체·배열·숫자로 오면 asString()
	// 강제 변환값(""·"123")이 계약 어휘로 새는 대신 null 로 떨궈 상위에서 폴백·생략되게 한다.
	private static String text(JsonNode node, String field) {
		JsonNode value = node.get(field);
		return value != null && value.isString() ? value.asString() : null;
	}

	private OffsetDateTime startOfTodayKst() {
		return LocalDate.now(KST).atStartOfDay(KST).toOffsetDateTime();
	}

	private static String feedState(OffsetDateTime last) {
		if (last == null) {
			return FeedStatus.STOPPED;
		}
		Duration since = Duration.between(last, OffsetDateTime.now());
		if (since.compareTo(FEED_FRESH) <= 0) {
			return FeedStatus.NORMAL;
		}
		return since.compareTo(FEED_STALE) <= 0 ? FeedStatus.DELAYED : FeedStatus.STOPPED;
	}

	// ── 쓰기(아직 mock — 실 설명 ID 는 mock 에 없어 404) ──

	public void updateFinal(String id, String finalText) {
		// 최종 제공 문구는 고객 노출 문면 — 빈 문구로의 교체는 막는다.
		requireText(finalText);
		ensure(applyMock(id, mid -> store.updateFinal(mid, finalText)));
	}

	public void stop(String id) {
		ensure(applyMock(id, store::stop));
	}

	public void moveToReview(String id) {
		ensure(applyMock(id, store::moveToReview));
	}

	public void approve(String id, String finalText) {
		requireText(finalText);
		ensure(applyMock(id, mid -> store.approve(mid, finalText)));
	}

	public void reject(String id, String note) {
		if (note == null || note.isBlank()) {
			// 반려 사유는 감사 재현의 최소 단서다(state-machine.md 정정·검수 규율).
			throw new GeneralException(ConsoleErrorStatus.REASON_REQUIRED);
		}
		ensure(applyMock(id, store::reject));
	}

	public void saveDraft(String id, String finalText) {
		// 임시 저장은 비우는 중간 상태를 허용한다 — null 만 거른다.
		if (finalText == null) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
		ensure(applyMock(id, mid -> store.saveDraft(mid, finalText)));
	}

	/**
	 * 쓰기 seam — mock 스토어는 long 키다. 원장 설명 ID(expr_LOCAL…)는 숫자가 아니라
	 * 파싱에서 걸러져 not-found(404)가 된다. 이 경로는 쓰기 전환 티켓에서 원장 전이로 교체된다.
	 */
	private boolean applyMock(String id, LongPredicate write) {
		long mockId;
		try {
			mockId = Long.parseLong(id);
		} catch (NumberFormatException e) {
			return false;
		}
		return write.test(mockId);
	}

	private static void requireText(String text) {
		if (text == null || text.isBlank()) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
	}

	private static void ensure(boolean found) {
		if (!found) {
			throw new GeneralException(ConsoleErrorStatus.EXPLANATION_NOT_FOUND);
		}
	}
}
