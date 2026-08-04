package com.edge.tenantconsole.service;

import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.entity.AnalysisItemEntity;
import com.edge.tenantconsole.entity.AnalysisItemStatusHistoryEntity;
import com.edge.tenantconsole.entity.PublicationEntity;
import com.edge.tenantconsole.entity.ScreeningCheckEntity;
import com.edge.tenantconsole.entity.ScreeningRuleEntity;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.model.Explanation;
import com.edge.tenantconsole.model.FeedStatus;
import com.edge.tenantconsole.model.FeedStatusAggregate;
import com.edge.tenantconsole.repository.AnalysisItemStatusHistoryRepository;
import com.edge.tenantconsole.repository.ExplanationLedgerRepository;
import com.edge.tenantconsole.repository.PublicationRepository;
import com.edge.tenantconsole.repository.PublishedSummaryRepository;
import com.edge.tenantconsole.repository.ScreeningCheckRepository;
import com.edge.tenantconsole.repository.ScreeningRuleRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

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

/**
 * 가격 변동 설명 표면 — 읽기(목록·반입 상태)와 사후 운영 쓰기(최종 문구 정정·제공
 * 중단·검수 이관)를 온프렘 원장으로 처리한다(ALPHA-607 읽기, ALPHA-613 쓰기 전환).
 * 판정 게이트(승인·반려)는 Review Queue(ReviewService) 소관이고, 이 표면은 현황판+사후
 * 운영에 한정된다(역할 분담, 사용자 결정 2026-07-29). 쓰기 3종은 ReviewService 와 동형
 * (@Transactional 단일 트랜잭션 + 읽은 상태를 WHERE 에 박은 가드 UPDATE 0행=409 +
 * status_history + console_action_log)이며, 행위자=SessionMember·clientIp 로 감사한다.
 *
 * <p>매핑(축소 계약, 사용자 결정 2026-07-29): name←etf_name, code←etf_ticker, status,
 * confidence←confidence_level(원값, ALPHA-634 확신도 배지), reviewReason←screening_check(REVIEW·BLOCK)→rule_type 파생,
 * receivedAt←received_at, evidence←evidences JSONB, original←summary,
 * final←publication.published_summary(널이면 summary). market·direction·changePct 는
 * 온프렘 원장에 없어(ALPHA-497 이연) 제거됐다.
 */
@Service
public class ExplanationService {

	private static final Logger log = LoggerFactory.getLogger(ExplanationService.class);

	private static final ZoneId KST = ZoneId.of("Asia/Seoul");

	/** 화면 노출 상태(labels.ts ServeStatus 6종) — 수신 전(RECEIVED)·무효화(INVALIDATED)는 제외. */
	private static final List<String> VISIBLE_STATUSES = List.of(
			"AUTO_PUBLISHED", "APPROVED", "REVIEW_REQUIRED", "BLOCKED", "REJECTED", "UNPUBLISHED");

	/** 사유가 붙는 분기 — 검수 대기·반려는 REVIEW 판정, 차단은 BLOCK 판정에서 파생한다. */
	private static final List<String> REASON_RESULTS = List.of("REVIEW", "BLOCK");

	/** 사유를 노출하는 상태 — 승인·자동 제공·중단은 과거 REVIEW 검사가 남아도 사유가 없다(승인 시 해소). */
	private static final Set<String> REASON_STATUSES = Set.of("REVIEW_REQUIRED", "BLOCKED", "REJECTED");

	/** 노출 중 상태 — 제공 중단(stop)은 이 상태에서만 의미가 있다(labels.ts PUBLISHED_STATUSES 와 정합). */
	private static final Set<String> SERVING_STATUSES = Set.of("AUTO_PUBLISHED", "APPROVED");

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
	private final PublicationRepository publicationRepository;
	private final AnalysisItemStatusHistoryRepository statusHistoryRepository;
	private final ConsoleActionLogService actionLog;
	private final ObjectMapper objectMapper = new ObjectMapper();

	public ExplanationService(ExplanationLedgerRepository ledger,
			ScreeningCheckRepository screeningCheckRepository,
			ScreeningRuleRepository screeningRuleRepository,
			PublishedSummaryRepository publishedSummaryRepository,
			PublicationRepository publicationRepository,
			AnalysisItemStatusHistoryRepository statusHistoryRepository,
			ConsoleActionLogService actionLog) {
		this.ledger = ledger;
		this.screeningCheckRepository = screeningCheckRepository;
		this.screeningRuleRepository = screeningRuleRepository;
		this.publishedSummaryRepository = publishedSummaryRepository;
		this.publicationRepository = publicationRepository;
		this.statusHistoryRepository = statusHistoryRepository;
		this.actionLog = actionLog;
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

	// ── evidences JSONB([{kind,title,source,published_at,source_uri}]) → 도메인 근거 목록 ──

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
					kind, text(node, "title"), text(node, "source"), parseTime(node),
					text(node, "source_uri")));
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

	// ── 사후 운영 쓰기(원장 전이·감사, ALPHA-613) — ReviewService 동형 ──

	/**
	 * 최종 제공 문구 정정 — 게시본(publication.published_summary)을 in-place 교체한다.
	 * 상태 전이가 없어 status_history 는 기록하지 않고(무전이 이벤트로 이력 원장을
	 * 오염시키지 않는다), 전후값만 감사 로그에 남긴다. 게시본 없는 항목은 409(NOT_PUBLISHED).
	 */
	@Transactional
	public void updateFinal(String id, String finalText, SessionMember actor, String clientIp) {
		// 최종 제공 문구는 고객 노출 문면 — 빈 문구로의 교체는 막는다(원장 조회 전).
		requireText(finalText);
		// 없는 설명은 404(전이 3종 공통) — 게시본 없음(409)과 구분한다.
		AnalysisItemEntity item = ledger.findById(id)
				.orElseThrow(() -> new GeneralException(ConsoleErrorStatus.EXPLANATION_NOT_FOUND));
		PublicationEntity published = publishedSummaryRepository
				.findFirstByAnalysisItemIdAndStatusOrderByPublicationIdDesc(id, "PUBLISHED")
				.orElseThrow(() -> new GeneralException(ConsoleErrorStatus.NOT_PUBLISHED));
		// 게시 스냅샷이 null 이면 고객에게 모델 원문(summary)이 노출됐다(published_summary
		// NULL = summary 노출, 스키마 규약) — 감사 before 는 실제 노출됐던 문구를 담아야
		// 민원·감사에서 "무엇을 무엇으로 고쳤나"가 재현된다(널 기록은 원문 유실).
		String before = published.getPublishedSummary() != null
				? published.getPublishedSummary() : item.getSummary();
		if (publicationRepository.updatePublishedSummary(id, finalText) == 0) {
			// 조회~갱신 사이 게시본이 내려간 경합 — 같은 트랜잭션에서 롤백된다.
			throw new GeneralException(ConsoleErrorStatus.NOT_PUBLISHED);
		}
		// summary 는 NOT NULL 이라 before 는 실질 비지 않지만, 방어적으로 널 허용 맵을 쓴다.
		Map<String, Object> detail = new LinkedHashMap<>();
		detail.put("before", before);
		detail.put("after", finalText);
		actionLog.record(actor, "EXPLANATION_FINAL_UPDATED", "ANALYSIS_ITEM", id, detail, clientIp);
		log.info("explanation final updated id={}", id);
	}

	/**
	 * 제공 중단 — 노출 중(AUTO_PUBLISHED|APPROVED) 항목을 UNPUBLISHED 로 내리고 게시본도
	 * 함께 내린다(사유·실행자·시각 감사 메타). 사유 필수(400), 노출 중이 아니면 409
	 * (NOT_SERVING), 게시본 불일치는 409(NOT_PUBLISHED)로 전이까지 롤백한다.
	 */
	@Transactional
	public void stop(String id, String reason, SessionMember actor, String clientIp) {
		if (reason == null || reason.isBlank()) {
			// 수동 중단 사유는 감사 재현의 최소 단서다(publication unpublish_reason 제약과 정합).
			throw new GeneralException(ConsoleErrorStatus.REASON_REQUIRED);
		}
		AnalysisItemEntity item = ledger.findById(id)
				.orElseThrow(() -> new GeneralException(ConsoleErrorStatus.EXPLANATION_NOT_FOUND));
		String fromStatus = item.getStatus();
		if (!SERVING_STATUSES.contains(fromStatus)) {
			throw new GeneralException(ConsoleErrorStatus.NOT_SERVING);
		}
		if (ledger.unpublish(id, fromStatus) == 0) {
			// 읽은 뒤 전이된 경합 — 노출 중이 아니게 됐다.
			throw new GeneralException(ConsoleErrorStatus.NOT_SERVING);
		}
		if (publicationRepository.unpublish(id, reason.trim(), actor.memberId()) == 0) {
			// 노출 중인데 게시본이 없는 불일치 — 전이까지 롤백(같은 트랜잭션).
			throw new GeneralException(ConsoleErrorStatus.NOT_PUBLISHED);
		}
		statusHistoryRepository.save(new AnalysisItemStatusHistoryEntity(id, fromStatus,
				"UNPUBLISHED", "MEMBER", actor.memberId(), reason.trim()));
		actionLog.record(actor, "EXPLANATION_STOPPED", "ANALYSIS_ITEM", id,
				Map.of("reason", reason.trim(), "fromStatus", fromStatus), clientIp);
		log.info("explanation stopped id={} fromStatus={}", id, fromStatus);
	}

	/**
	 * 검수 이관 — 점검 차단(BLOCKED) 항목을 검수 대기(REVIEW_REQUIRED)로 되돌려 Review
	 * Queue 로 유입시킨다. 차단이 아니면 409(NOT_BLOCKED). 게시 무접촉(차단은 미게시).
	 */
	@Transactional
	public void moveToReview(String id, SessionMember actor, String clientIp) {
		if (ledger.findById(id).isEmpty()) {
			throw new GeneralException(ConsoleErrorStatus.EXPLANATION_NOT_FOUND);
		}
		if (ledger.moveBlockedToReview(id) == 0) {
			throw new GeneralException(ConsoleErrorStatus.NOT_BLOCKED);
		}
		statusHistoryRepository.save(new AnalysisItemStatusHistoryEntity(id, "BLOCKED",
				"REVIEW_REQUIRED", "MEMBER", actor.memberId(), null));
		actionLog.record(actor, "EXPLANATION_MOVED_TO_REVIEW", "ANALYSIS_ITEM", id, null, clientIp);
		log.info("explanation moved to review id={}", id);
	}

	private static void requireText(String text) {
		if (text == null || text.isBlank()) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
	}
}
