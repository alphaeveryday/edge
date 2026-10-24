package com.edge.tenantconsole.service;

import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.entity.AnalysisItemEntity;
import com.edge.tenantconsole.entity.AnalysisItemStatusHistoryEntity;
import com.edge.tenantconsole.entity.MemberEntity;
import com.edge.tenantconsole.entity.PolicyVersionEntity;
import com.edge.tenantconsole.entity.ReviewTaskEntity;
import com.edge.tenantconsole.entity.ScreeningCheckEntity;
import com.edge.tenantconsole.entity.ScreeningRuleEntity;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.model.ReviewItem;
import com.edge.tenantconsole.model.ReviewItemDetail;
import com.edge.tenantconsole.model.ReviewListEntry;
import com.edge.tenantconsole.repository.AnalysisItemStatusHistoryRepository;
import com.edge.tenantconsole.repository.MemberRepository;
import com.edge.tenantconsole.repository.PublicationRepository;
import com.edge.tenantconsole.repository.ReviewItemRepository;
import com.edge.tenantconsole.repository.ReviewTaskRepository;
import com.edge.tenantconsole.repository.ScreeningCheckRepository;
import com.edge.tenantconsole.repository.PolicyVersionRepository;
import com.edge.tenantconsole.repository.ScreeningRuleRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import tools.jackson.databind.ObjectMapper;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 검수 오케스트레이션(state-machine.md·ALPHA-437): 승인 = REVIEW_REQUIRED → APPROVED +
 * 재발행(수정 승인은 편집 문구를 publication.published_summary 로 게시), 반려 =
 * REJECTED(사유 필수), 차단 = BLOCKED(사유 필수, 게시 무접촉). 전이·게시·review_task
 * 기록·감사(console_action_log)는 한 트랜잭션 — 어중간한 상태·기록 없는 결정을 남기지
 * 않는다. 편집 원문은 analysis_item 에 보존된다(review_task DDL 규약).
 */
@Service
public class ReviewService {

	private static final Logger log = LoggerFactory.getLogger(ReviewService.class);
	/** 룰 무관 REVIEW(자동 제공 스위치·출처 임계 미충족)의 파생 사유 마커 — rule_type
	 * 어휘(스키마 CHECK)와 겹치지 않는 별도 상수. 버리면 정상 유입 항목이 사유 공백이 된다. */
	private static final String AUTO_PUBLISH_CRITERIA_REASON = "AUTO_PUBLISH_CRITERIA";

	private final ReviewItemRepository reviewItemRepository;
	private final PublicationRepository publicationRepository;
	private final ReviewTaskRepository reviewTaskRepository;
	private final AnalysisItemStatusHistoryRepository statusHistoryRepository;
	private final ScreeningCheckRepository screeningCheckRepository;
	private final ScreeningRuleRepository screeningRuleRepository;
	private final PolicyVersionRepository policyVersionRepository;
	private final MemberRepository memberRepository;
	private final ConsoleActionLogService actionLog;
	private final ObjectMapper objectMapper = new ObjectMapper();

	public ReviewService(ReviewItemRepository reviewItemRepository,
			PublicationRepository publicationRepository,
			ReviewTaskRepository reviewTaskRepository,
			AnalysisItemStatusHistoryRepository statusHistoryRepository,
			ScreeningCheckRepository screeningCheckRepository,
			ScreeningRuleRepository screeningRuleRepository,
			PolicyVersionRepository policyVersionRepository,
			MemberRepository memberRepository,
			ConsoleActionLogService actionLog) {
		this.reviewItemRepository = reviewItemRepository;
		this.publicationRepository = publicationRepository;
		this.reviewTaskRepository = reviewTaskRepository;
		this.statusHistoryRepository = statusHistoryRepository;
		this.screeningCheckRepository = screeningCheckRepository;
		this.screeningRuleRepository = screeningRuleRepository;
		this.policyVersionRepository = policyVersionRepository;
		this.memberRepository = memberRepository;
		this.actionLog = actionLog;
	}

	public List<ReviewItem> list(String status, int limit, int offset) {
		return reviewItemRepository.pageByStatus(status, limit, offset)
				.stream().map(ReviewItem::from).toList();
	}

	/** 목록 + 파생 검수 사유(ALPHA-436) — 사유는 배치 조회로 해석한다(항목당 N+1 금지). */
	public List<ReviewListEntry> listWithReasons(String status, int limit, int offset) {
		List<ReviewItem> items = list(status, limit, offset);
		List<String> ids = items.stream().map(ReviewItem::explanationResultId).toList();
		// 검사 행은 한 번만 읽는다 — 사유와 게이트 재료가 같은 행에서 나온다.
		List<ScreeningCheckEntity> reviewChecks = ids.isEmpty() ? List.of()
				: screeningCheckRepository.findByAnalysisItemIdInAndResultOrderByScreeningCheckId(ids, "REVIEW");
		Map<String, List<String>> reasons = reviewReasonsFor(reviewChecks);
		Map<String, List<ReviewListEntry.GateCheck>> gateChecks = gateChecksFor(reviewChecks);
		return items.stream()
				.map(i -> new ReviewListEntry(i,
						reasons.getOrDefault(i.explanationResultId(), List.of()),
						gateChecks.getOrDefault(i.explanationResultId(), List.of())))
				.toList();
	}

	/** 검수 상세(ALPHA-436, 구 439 흡수) — 근거·사유·검사 결과·상태 이력을 한 번에. */
	public ReviewItemDetail detail(String explanationResultId) {
		AnalysisItemEntity entity = reviewItemRepository.findById(explanationResultId)
				.orElseThrow(() -> new GeneralException(ConsoleErrorStatus.REVIEW_ITEM_NOT_FOUND));
		List<ScreeningCheckEntity> checks =
				screeningCheckRepository.findByAnalysisItemIdOrderByScreeningCheckId(explanationResultId);
		Map<Long, String> ruleTypes = ruleTypesById(
				checks.stream().map(ScreeningCheckEntity::getScreeningRuleId).filter(id -> id != null).toList());
		List<String> reviewReasons = checks.stream()
				.filter(c -> "REVIEW".equals(c.getResult()))
				.map(c -> reasonOf(c, ruleTypes))
				.filter(t -> t != null).distinct().toList();
		// 판정 당시 정책 — 검수 사유를 "설정한 기준의 문구"로 쓰려면 기준이 필요하고,
		// 그 출처는 기록된 버전이다(matchedText 는 실측값이라 기준을 담지 않는다, ALPHA-774).
		Map<Long, PolicyVersionEntity> versions = policyVersionsById(
				checks.stream().map(ScreeningCheckEntity::getPolicyVersionId)
						.filter(id -> id != null).toList());
		List<ReviewItemDetail.ScreeningCheckView> checkViews = checks.stream()
				.map(c -> {
					PolicyVersionEntity version = c.getPolicyVersionId() == null
							? null : versions.get(c.getPolicyVersionId());
					return new ReviewItemDetail.ScreeningCheckView(c.getResult(),
							c.getScreeningRuleId() == null ? null : ruleTypes.get(c.getScreeningRuleId()),
							c.getMatchedText(), c.getCheckedAt(),
							version == null ? null : version.getVersionNo(),
							version == null ? null : version.getMinSourceCount(),
							version == null ? null : version.getMinConfidence());
				})
				.toList();
		List<AnalysisItemStatusHistoryEntity> history = statusHistoryRepository
				.findByAnalysisItemIdOrderByStatusHistoryIdAsc(explanationResultId);
		// 행위자 이름은 일괄 해석 — 이력이 길어질수록 행마다 findById 는 N+1 이 된다.
		Map<Long, String> memberNames = new HashMap<>();
		if (history.stream().anyMatch(h -> h.getActorId() != null)) {
			for (MemberEntity member : memberRepository.findAllOrderByMemberId()) {
				memberNames.put(member.getMemberId(), member.getName());
			}
		}
		List<ReviewItemDetail.StatusChange> changes = history.stream()
				.map(h -> new ReviewItemDetail.StatusChange(h.getFromStatus(), h.getToStatus(),
						h.getActorType(),
						h.getActorId() == null ? null : memberNames.get(h.getActorId()),
						h.getReason(), h.getOccurredAt()))
				.toList();
		return new ReviewItemDetail(ReviewItem.from(entity),
				objectMapper.readTree(entity.getEvidences() == null ? "[]" : entity.getEvidences()),
				reviewReasons, checkViews, changes);
	}

	/**
	 * 목록의 룰 무관 REVIEW 재료 — 상세와 같은 문구를 만들려면 판정 당시 기준이 필요하다
	 * (ALPHA-774). 목록만 "자동 제공 기준 미충족"으로 뭉치면 같은 항목의 사유가 두 화면에서
	 * 달라진다. 검사·버전 모두 일괄 조회다(항목당 조회는 N+1).
	 */
	private Map<String, List<ReviewListEntry.GateCheck>> gateChecksFor(
			List<ScreeningCheckEntity> reviewChecks) {
		List<ScreeningCheckEntity> checks = reviewChecks.stream()
				.filter(c -> c.getScreeningRuleId() == null)
				.toList();
		Map<Long, PolicyVersionEntity> versions = policyVersionsById(
				checks.stream().map(ScreeningCheckEntity::getPolicyVersionId)
						.filter(id -> id != null).toList());
		Map<String, List<ReviewListEntry.GateCheck>> byItem = new LinkedHashMap<>();
		for (ScreeningCheckEntity check : checks) {
			PolicyVersionEntity version = check.getPolicyVersionId() == null
					? null : versions.get(check.getPolicyVersionId());
			byItem.computeIfAbsent(check.getAnalysisItemId(), k -> new ArrayList<>())
					.add(new ReviewListEntry.GateCheck(check.getMatchedText(),
							version == null ? null : version.getMinSourceCount(),
							version == null ? null : version.getMinConfidence()));
		}
		return byItem;
	}

	/** 검사 행이 가리키는 정책 버전 일괄 조회 — 행마다 조회하면 N+1 이다. */
	private Map<Long, PolicyVersionEntity> policyVersionsById(Collection<Long> policyVersionIds) {
		if (policyVersionIds.isEmpty()) {
			return Map.of();
		}
		Map<Long, PolicyVersionEntity> byId = new HashMap<>();
		for (PolicyVersionEntity version : policyVersionRepository
				.findByPolicyVersionIdIn(Set.copyOf(policyVersionIds))) {
			byId.put(version.getPolicyVersionId(), version);
		}
		return byId;
	}

	/** 항목들의 검수 사유 파생 — screening_check(result='REVIEW') → screening_rule.rule_type. */
	private Map<String, List<String>> reviewReasonsFor(List<ScreeningCheckEntity> checks) {
		Map<Long, String> ruleTypes = ruleTypesById(
				checks.stream().map(ScreeningCheckEntity::getScreeningRuleId).filter(id -> id != null).toList());
		Map<String, List<String>> byItem = new LinkedHashMap<>();
		for (ScreeningCheckEntity check : checks) {
			String type = reasonOf(check, ruleTypes);
			if (type == null) {
				continue;
			}
			List<String> list = byItem.computeIfAbsent(check.getAnalysisItemId(), k -> new ArrayList<>());
			if (!list.contains(type)) {
				list.add(type);
			}
		}
		return byItem;
	}

	private static String reasonOf(ScreeningCheckEntity check, Map<Long, String> ruleTypes) {
		if (check.getScreeningRuleId() == null) {
			return AUTO_PUBLISH_CRITERIA_REASON;
		}
		return ruleTypes.get(check.getScreeningRuleId());
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

	@Transactional
	public void approve(String explanationResultId, String note, SessionMember actor,
			String clientIp) {
		approveInternal(explanationResultId, null, blankToNull(note), actor, clientIp);
	}

	/**
	 * 수정 승인 — edited_summary 필수(누락·공백 400). 편집 문구가 published_summary 로
	 * 게시되고 review_task 에 EDITED_APPROVED 로 영속된다. 전용 라우트인 이유: 선택
	 * 바디로 겸하면 unknown 필드 무시 탓에 편집 필드 오타가 일반 승인으로 강등된다.
	 */
	@Transactional
	public void approveEdited(String explanationResultId, String editedSummary, String note,
			SessionMember actor, String clientIp) {
		if (editedSummary == null || editedSummary.isBlank()) {
			// 편집 없는 수정 승인은 모순 — 원문이 게시되는데 기록만 EDITED 가 되는 것을 막는다.
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
		approveInternal(explanationResultId, editedSummary.trim(), blankToNull(note), actor,
				clientIp);
	}

	private void approveInternal(String explanationResultId, String editedSummary, String note,
			SessionMember actor, String clientIp) {
		boolean edited = editedSummary != null;
		ReviewItem item = reviewItemRepository.findById(explanationResultId).map(ReviewItem::from)
				.orElseThrow(() -> new GeneralException(ConsoleErrorStatus.REVIEW_ITEM_NOT_FOUND));
		if (item.etfTicker() == null) {
			// ticker 는 게시(서빙 키) 필수 — 없는 항목은 승인해도 노출할 수 없다.
			throw new GeneralException(ConsoleErrorStatus.NOT_PUBLISHABLE);
		}
		if (reviewItemRepository.decide(explanationResultId, "APPROVED") == 0) {
			throw new GeneralException(ConsoleErrorStatus.NOT_IN_REVIEW);
		}
		// 게시 문구 스냅샷 — 수정 승인은 편집 요약, 일반 승인은 원문(published_summary 규약).
		String publishedSummary = editedSummary != null ? editedSummary : item.summary();
		if (publicationRepository.publish(explanationResultId, item.etfTicker(), item.tradeDate(),
				item.explanationAsOf(), publishedSummary) == 0) {
			// 같은 스냅샷 이중 게시 — 전이도 함께 롤백된다(같은 트랜잭션). 다스냅샷 공존
			// (ALPHA-743)이라 다른 발화의 게시가 승인을 막지 않는다.
			throw new GeneralException(ConsoleErrorStatus.GRAIN_OCCUPIED);
		}
		// edited_headline 은 받지 않는다(서빙 노출 경로 없음 — ReviewEditedApproveRequest 주석).
		reviewTaskRepository.save(new ReviewTaskEntity(explanationResultId,
				edited ? "EDITED_APPROVED" : "APPROVED", actor.memberId(),
				null, editedSummary, note, OffsetDateTime.now()));
		// 자기 전이는 같은 트랜잭션에서 이력 원장에 기록한다(status_history writer 규약).
		statusHistoryRepository.save(new AnalysisItemStatusHistoryEntity(explanationResultId,
				"REVIEW_REQUIRED", "APPROVED", "MEMBER", actor.memberId(), note));
		actionLog.record(actor, edited ? "REVIEW_EDITED_APPROVED" : "REVIEW_APPROVED",
				"ANALYSIS_ITEM", explanationResultId,
				Map.of("ticker", item.etfTicker(), "tradeDate", String.valueOf(item.tradeDate())),
				clientIp);
		log.info("review approved id={} ticker={} trade_date={} edited={}",
				explanationResultId, item.etfTicker(), item.tradeDate(), edited);
	}

	@Transactional
	public void reject(String explanationResultId, String reason, SessionMember actor,
			String clientIp) {
		if (reason == null || reason.isBlank()) {
			// 반려 사유는 감사 재현의 최소 단서다(state-machine.md 정정·검수 규율).
			throw new GeneralException(ConsoleErrorStatus.REASON_REQUIRED);
		}
		if (reviewItemRepository.findById(explanationResultId).isEmpty()) {
			throw new GeneralException(ConsoleErrorStatus.REVIEW_ITEM_NOT_FOUND);
		}
		if (reviewItemRepository.decide(explanationResultId, "REJECTED") == 0) {
			throw new GeneralException(ConsoleErrorStatus.NOT_IN_REVIEW);
		}
		reviewTaskRepository.save(new ReviewTaskEntity(explanationResultId, "REJECTED",
				actor.memberId(), null, null, reason, OffsetDateTime.now()));
		statusHistoryRepository.save(new AnalysisItemStatusHistoryEntity(explanationResultId,
				"REVIEW_REQUIRED", "REJECTED", "MEMBER", actor.memberId(), reason));
		actionLog.record(actor, "REVIEW_REJECTED", "ANALYSIS_ITEM", explanationResultId,
				Map.of("reason", reason), clientIp);
		log.info("review rejected id={} reason={}", explanationResultId, reason);
	}

	@Transactional
	public void block(String explanationResultId, String reason, SessionMember actor,
			String clientIp) {
		if (reason == null || reason.isBlank()) {
			// 차단 사유도 반려와 같은 규율 — 감사 재현의 최소 단서다.
			throw new GeneralException(ConsoleErrorStatus.REASON_REQUIRED);
		}
		if (reviewItemRepository.findById(explanationResultId).isEmpty()) {
			throw new GeneralException(ConsoleErrorStatus.REVIEW_ITEM_NOT_FOUND);
		}
		if (reviewItemRepository.decide(explanationResultId, "BLOCKED") == 0) {
			throw new GeneralException(ConsoleErrorStatus.NOT_IN_REVIEW);
		}
		// 차단은 게시 무접촉·review_task 어휘 밖(ck_review_task_status) — 사유·주체는
		// 이력 원장과 감사 로그가 담는다.
		statusHistoryRepository.save(new AnalysisItemStatusHistoryEntity(explanationResultId,
				"REVIEW_REQUIRED", "BLOCKED", "MEMBER", actor.memberId(), reason));
		actionLog.record(actor, "REVIEW_BLOCKED", "ANALYSIS_ITEM", explanationResultId,
				Map.of("reason", reason), clientIp);
		log.info("review blocked id={} reason={}", explanationResultId, reason);
	}

	private static String blankToNull(String value) {
		return value == null || value.isBlank() ? null : value.trim();
	}
}
