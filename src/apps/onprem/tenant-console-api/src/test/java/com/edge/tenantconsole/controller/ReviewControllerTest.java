package com.edge.tenantconsole.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.entity.AnalysisItemEntity;
import com.edge.tenantconsole.entity.AnalysisItemStatusHistoryEntity;
import com.edge.tenantconsole.entity.ReviewTaskEntity;
import com.edge.tenantconsole.repository.AnalysisItemStatusHistoryRepository;
import com.edge.tenantconsole.repository.PublicationRepository;
import com.edge.tenantconsole.repository.ReviewItemRepository;
import com.edge.tenantconsole.repository.ReviewTaskRepository;
import com.edge.tenantconsole.service.ConsoleActionLogService;
import com.edge.tenantconsole.service.ReviewService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.data.domain.Limit;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 검수 계약(state-machine.md·ALPHA-437)을 검증한다: 승인 = 전이+재발행+기록+감사가
 * 한 단위(수정 승인은 수정 문구로 게시·EDITED_APPROVED), 반려·차단 = 사유 필수,
 * REVIEW_REQUIRED 밖 항목은 409(동시 결정 수렴), grain 선점은 409. 모든 결정은
 * 세션 주체(검수자)가 review_task·감사 로그에 남아야 감사 재현이 가능하다.
 * Boot 4 는 @WebMvcTest 슬라이스가 없어 standaloneSetup 을 쓴다. 리포지토리(JPA)는 좁은
 * 인터페이스라 페이크로 스텁한다 — 실 DB 경로는 Testcontainers 통합 테스트.
 */
class ReviewControllerTest {

	private static final AnalysisItemEntity PENDING = new AnalysisItemEntity(
			"er-rev-1", "069500", "KODEX 200", LocalDate.of(2026, 7, 15),
			"정정 요약", null, "LOW", "REVIEW_REQUIRED", "er-0", "근거 공시 정정",
			OffsetDateTime.of(2026, 7, 15, 17, 0, 0, 0, ZoneOffset.ofHours(9)));

	private static final SessionMember REVIEWER =
			new SessionMember(2L, "reviewer@demo.edge.local", "데모 검수자", "COMPLIANCE_REVIEWER");

	private static final class StubItems implements ReviewItemRepository {
		AnalysisItemEntity item = PENDING;
		boolean decideResult = true;
		final List<String> decisions = new ArrayList<>();

		@Override
		public List<AnalysisItemEntity> findByStatusOrderByReceivedAtAsc(String status, Limit limit) {
			return item != null && item.getStatus().equals(status) ? List.of(item) : List.of();
		}

		@Override
		public Optional<AnalysisItemEntity> findById(String id) {
			return Optional.ofNullable(
					item != null && item.getExplanationResultId().equals(id) ? item : null);
		}

		@Override
		public int decide(String id, String decidedStatus) {
			decisions.add(id + ":" + decidedStatus);
			return decideResult ? 1 : 0;
		}
	}

	private static final class StubPublications implements PublicationRepository {
		boolean publishResult = true;
		final List<String> published = new ArrayList<>();
		String capturedSummary;

		@Override
		public int publish(String analysisItemId, String etfTicker, LocalDate tradeDate,
				String publishedSummary) {
			published.add(analysisItemId);
			this.capturedSummary = publishedSummary;
			return publishResult ? 1 : 0;
		}
	}

	private static final class StubTasks implements ReviewTaskRepository {
		final List<ReviewTaskEntity> saved = new ArrayList<>();

		@Override
		public ReviewTaskEntity save(ReviewTaskEntity task) {
			saved.add(task);
			return task;
		}
	}

	private static final class StubHistory implements AnalysisItemStatusHistoryRepository {
		final List<AnalysisItemStatusHistoryEntity> saved = new ArrayList<>();

		@Override
		public AnalysisItemStatusHistoryEntity save(AnalysisItemStatusHistoryEntity history) {
			saved.add(history);
			return history;
		}
	}

	/** 감사 기록 대역 — DB 없이 record 호출을 캡처한다(MemberServiceTest 와 동일 패턴). */
	private static final class RecordingActionLog extends ConsoleActionLogService {
		record Entry(SessionMember actor, String action, String targetType, String targetId,
				Map<String, Object> detail, String clientIp) {
		}

		final List<Entry> entries = new ArrayList<>();

		RecordingActionLog() {
			super(null, null);
		}

		@Override
		public void record(SessionMember actor, String action, String targetType, String targetId,
				Map<String, Object> detail, String clientIp) {
			entries.add(new Entry(actor, action, targetType, targetId, detail, clientIp));
		}
	}

	private StubItems items;
	private StubPublications publications;
	private StubTasks tasks;
	private StubHistory history;
	private RecordingActionLog actionLog;
	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		items = new StubItems();
		publications = new StubPublications();
		tasks = new StubTasks();
		history = new StubHistory();
		actionLog = new RecordingActionLog();
		mvc = MockMvcBuilders
				.standaloneSetup(new ReviewController(
						new ReviewService(items, publications, tasks, history, actionLog)))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 검수_대기_목록은_계약_형상이다() throws Exception {
		// WHY: 콘솔 화면(review 도메인 repository.real)이 이 필드명으로 렌더링한다.
		mvc.perform(get("/api/v1/review/items"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.code").value("COMMON200"))
				.andExpect(jsonPath("$.result[0].explanation_result_id").value("er-rev-1"))
				.andExpect(jsonPath("$.result[0].status").value("REVIEW_REQUIRED"))
				.andExpect(jsonPath("$.result[0].supersedes_item_id").value("er-0"))
				.andExpect(jsonPath("$.result[0].correction_reason").value("근거 공시 정정"));
	}

	@Test
	void 승인은_전이_재발행_기록_감사를_함께_수행한다() throws Exception {
		// WHY: 검수 승인 후에만 재발행(state-machine.md) — 전이만 되고 게시·기록·감사가
		// 빠지면 고객 미노출이거나 감사 재현이 불가능하다(ALPHA-437). 게시 문구는 편집이
		// 없으므로 원문 스냅샷이어야 한다.
		mvc.perform(post("/api/v1/review/items/er-rev-1/approve")
						.sessionAttr(SessionMember.SESSION_KEY, REVIEWER))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true));

		assertThat(items.decisions).containsExactly("er-rev-1:APPROVED");
		assertThat(publications.published).containsExactly("er-rev-1");
		assertThat(publications.capturedSummary).isEqualTo("정정 요약");  // 원문 스냅샷
		assertThat(tasks.saved).singleElement().satisfies(t -> {
			assertThat(t.getStatus()).isEqualTo("APPROVED");
			assertThat(t.getAnalysisItemId()).isEqualTo("er-rev-1");
			assertThat(t.getReviewerId()).isEqualTo(2L);       // 세션 주체가 검수자
			assertThat(t.getEditedSummary()).isNull();
			assertThat(t.getDecidedAt()).isNotNull();          // ck_review_task_decided
		});
		assertThat(actionLog.entries).singleElement().satisfies(e -> {
			assertThat(e.action()).isEqualTo("REVIEW_APPROVED");
			assertThat(e.actor()).isEqualTo(REVIEWER);
			assertThat(e.targetId()).isEqualTo("er-rev-1");
		});
		// 상태 변경 이력 원장(analysis_item_status_history) — 자기 전이는 같은 트랜잭션에서
		// MEMBER 로 기록한다(스키마 COMMENT 의 writer 규약).
		assertThat(history.saved).singleElement().satisfies(h -> {
			assertThat(h.getFromStatus()).isEqualTo("REVIEW_REQUIRED");
			assertThat(h.getToStatus()).isEqualTo("APPROVED");
			assertThat(h.getActorType()).isEqualTo("MEMBER");
			assertThat(h.getActorId()).isEqualTo(2L);
		});
	}

	@Test
	void 수정_승인은_수정_문구로_게시하고_EDITED_APPROVED_를_남긴다() throws Exception {
		// WHY: 수정 승인의 노출 경로는 publication.published_summary 스냅샷(DDL 주석) —
		// analysis_item 원문은 보존되고, 편집본·의견은 review_task 에 영속된다.
		mvc.perform(post("/api/v1/review/items/er-rev-1/approve")
						.sessionAttr(SessionMember.SESSION_KEY, REVIEWER)
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"edited_summary\":\"수정된 요약\",\"note\":\"문구 순화\"}"))
				.andExpect(status().isOk());

		assertThat(publications.capturedSummary).isEqualTo("수정된 요약");
		assertThat(tasks.saved).singleElement().satisfies(t -> {
			assertThat(t.getStatus()).isEqualTo("EDITED_APPROVED");
			assertThat(t.getEditedSummary()).isEqualTo("수정된 요약");
			assertThat(t.getReviewNote()).isEqualTo("문구 순화");
		});
		assertThat(actionLog.entries).singleElement()
				.satisfies(e -> assertThat(e.action()).isEqualTo("REVIEW_EDITED_APPROVED"));
	}

	@Test
	void 공백뿐인_수정_문구는_400_이고_일반_승인으로_강등되지_않는다() throws Exception {
		// WHY: 편집 의도가 공백 강제 변환으로 조용히 일반 승인이 되면(coerce-to-passing)
		// 감사와 실제 노출이 어긋난다 — ck_review_task_edited_content 와 같은 규율을 앱이 먼저.
		mvc.perform(post("/api/v1/review/items/er-rev-1/approve")
						.sessionAttr(SessionMember.SESSION_KEY, REVIEWER)
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"edited_summary\":\"   \"}"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("CNSL4003"));
		assertThat(items.decisions).isEmpty();
		assertThat(publications.published).isEmpty();
		assertThat(tasks.saved).isEmpty();
		assertThat(actionLog.entries).isEmpty();
	}

	@Test
	void 검수_대기가_아니면_승인은_409다() throws Exception {
		// WHY: 동시 검수·재클릭이 이중 결정으로 이어지면 안 된다 — 전이 0행 = 충돌 수렴.
		items.decideResult = false;
		mvc.perform(post("/api/v1/review/items/er-rev-1/approve")
						.sessionAttr(SessionMember.SESSION_KEY, REVIEWER))
				.andExpect(status().isConflict())
				.andExpect(jsonPath("$.code").value("CNSL4090"));
		assertThat(publications.published).isEmpty();
		assertThat(tasks.saved).isEmpty();
		assertThat(actionLog.entries).isEmpty();
	}

	@Test
	void grain_선점_시_승인은_409_이고_기록과_감사가_남지_않는다() throws Exception {
		// WHY: 전이·게시·기록은 한 트랜잭션 — 게시가 실패한 승인이 기록·감사에만 남으면
		// 감사가 실제 상태와 어긋난다(전이는 롤백으로 함께 되돌아간다).
		publications.publishResult = false;
		mvc.perform(post("/api/v1/review/items/er-rev-1/approve")
						.sessionAttr(SessionMember.SESSION_KEY, REVIEWER))
				.andExpect(status().isConflict())
				.andExpect(jsonPath("$.code").value("CNSL4091"));
		assertThat(tasks.saved).isEmpty();
		assertThat(history.saved).isEmpty();
		assertThat(actionLog.entries).isEmpty();
	}

	@Test
	void 반려는_사유가_필수고_REJECTED_기록과_감사를_남긴다() throws Exception {
		// WHY: 반려 사유는 감사 재현의 최소 단서 — 빈 사유가 계약을 통과하면 안 되고,
		// 유효한 반려는 review_task(사유 포함)·감사 로그에 남아야 한다(ALPHA-437).
		mvc.perform(post("/api/v1/review/items/er-rev-1/reject")
						.sessionAttr(SessionMember.SESSION_KEY, REVIEWER)
						.contentType(MediaType.APPLICATION_JSON).content("{\"reason\":\"  \"}"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("CNSL4001"));

		mvc.perform(post("/api/v1/review/items/er-rev-1/reject")
						.sessionAttr(SessionMember.SESSION_KEY, REVIEWER)
						.contentType(MediaType.APPLICATION_JSON).content("{\"reason\":\"근거 불충분\"}"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true));
		assertThat(items.decisions).containsExactly("er-rev-1:REJECTED");
		assertThat(tasks.saved).singleElement().satisfies(t -> {
			assertThat(t.getStatus()).isEqualTo("REJECTED");
			assertThat(t.getReviewNote()).isEqualTo("근거 불충분");
			assertThat(t.getReviewerId()).isEqualTo(2L);
		});
		assertThat(actionLog.entries).singleElement()
				.satisfies(e -> assertThat(e.action()).isEqualTo("REVIEW_REJECTED"));
		assertThat(history.saved).singleElement().satisfies(h -> {
			assertThat(h.getToStatus()).isEqualTo("REJECTED");
			assertThat(h.getReason()).isEqualTo("근거 불충분");
		});
	}

	@Test
	void 차단은_BLOCKED_전이와_감사를_남기고_게시하지_않는다() throws Exception {
		// WHY: 차단 = 고객 화면 비노출 확정(state-machine.md) — publication 무접촉이어야
		// 하고, 사유·주체는 감사 로그가 담는다(review_task 어휘엔 차단이 없다).
		mvc.perform(post("/api/v1/review/items/er-rev-1/block")
						.sessionAttr(SessionMember.SESSION_KEY, REVIEWER)
						.contentType(MediaType.APPLICATION_JSON).content("{\"reason\":\"부정확한 수치\"}"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true));

		assertThat(items.decisions).containsExactly("er-rev-1:BLOCKED");
		assertThat(publications.published).isEmpty();
		assertThat(tasks.saved).isEmpty();
		assertThat(actionLog.entries).singleElement().satisfies(e -> {
			assertThat(e.action()).isEqualTo("REVIEW_BLOCKED");
			assertThat(e.actor()).isEqualTo(REVIEWER);
			assertThat(e.detail()).containsEntry("reason", "부정확한 수치");
		});
		assertThat(history.saved).singleElement().satisfies(h -> {
			assertThat(h.getFromStatus()).isEqualTo("REVIEW_REQUIRED");
			assertThat(h.getToStatus()).isEqualTo("BLOCKED");
			assertThat(h.getActorType()).isEqualTo("MEMBER");
			assertThat(h.getReason()).isEqualTo("부정확한 수치");
		});
	}

	@Test
	void 차단_사유_blank_는_400_이고_전이가_없다() throws Exception {
		mvc.perform(post("/api/v1/review/items/er-rev-1/block")
						.sessionAttr(SessionMember.SESSION_KEY, REVIEWER)
						.contentType(MediaType.APPLICATION_JSON).content("{\"reason\":\" \"}"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("CNSL4001"));
		assertThat(items.decisions).isEmpty();
	}

	@Test
	void 이미_처리된_항목의_차단은_409다() throws Exception {
		items.decideResult = false;
		mvc.perform(post("/api/v1/review/items/er-rev-1/block")
						.sessionAttr(SessionMember.SESSION_KEY, REVIEWER)
						.contentType(MediaType.APPLICATION_JSON).content("{\"reason\":\"사유\"}"))
				.andExpect(status().isConflict())
				.andExpect(jsonPath("$.code").value("CNSL4090"));
		assertThat(actionLog.entries).isEmpty();
	}

	@Test
	void 미지의_항목은_404_잘못된_상태_필터는_400이다() throws Exception {
		mvc.perform(post("/api/v1/review/items/no-such/approve")
						.sessionAttr(SessionMember.SESSION_KEY, REVIEWER))
				.andExpect(status().isNotFound())
				.andExpect(jsonPath("$.code").value("CNSL4040"));
		mvc.perform(get("/api/v1/review/items").param("status", "WHATEVER"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("CNSL4002"));
	}
}
