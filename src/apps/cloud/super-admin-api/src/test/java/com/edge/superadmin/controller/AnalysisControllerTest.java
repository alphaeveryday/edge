package com.edge.superadmin.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.superadmin.auth.SessionOperator;
import com.edge.superadmin.repository.AnalysisRepository.AnalysisRow;
import com.edge.superadmin.repository.AnalysisRepository.EvidenceRow;
import com.edge.superadmin.service.AnalysisService;
import com.edge.superadmin.support.FakeAnalysisRepository;
import com.edge.superadmin.support.FakeAnalysisWriteRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * UI 계약(super-admin-ui analyses 도메인) 검증: 원장 어휘가 UI 어휘로 번역되고(ALPHA-601),
 * 쓰기는 무효화 단독(ALPHA-440 — 구 3종 오버레이는 ALPHA-737 은퇴)이다. <b>사유 필수</b>,
 * 작업자(세션 운영자)·사유가 저장 계층까지 흐르고, 게시 상태(publicationStatus)는 실행
 * 상태와 별개 축으로 원장 어휘 그대로 노출된다(무효화 버튼 활성 조건).
 */
class AnalysisControllerTest {

	/**
	 * 하락 −3.42% · 완료 · 근거 2건(뉴스·공시) — 번역 전부를 태우는 행.
	 * 총 건수는 57 로 둔다: 표시 상한에 잘린 런에서 화면이 표시 건수가 아니라 총 건수를
	 * 말하는지 확인하려면 둘이 달라야 한다.
	 */
	private static final AnalysisRow COMPLETED_ROW = new AnalysisRow(
			"run-1", "KODEX 반도체", "091160", "XKRX", -0.0342, "SUCCEEDED",
			OffsetDateTime.parse("2026-07-27T15:40:00+09:00"),
			OffsetDateTime.parse("2026-07-27T15:52:00+09:00"),
			"반도체 업황 회복 기대가 확산되며 상승.", "HIGH", "PUBLISHED",
			List.of(new EvidenceRow("NEWS", "반도체 수출 반등", "BIGKINDS",
							OffsetDateTime.parse("2026-07-27T09:10:00+09:00")),
					new EvidenceRow("DISCLOSURE", null, "DART", null)),
			57);

	/** 결과 행이 아직 없는 런 — summary·confidence 가 null 로 온다. */
	private static final AnalysisRow PENDING_ROW = new AnalysisRow(
			"run-2", "TIGER 2차전지", "305540", "XKRX", 0.0518, "RUNNING",
			OffsetDateTime.parse("2026-07-27T15:40:00+09:00"), null, null, null, null,
			List.of(), 0);

	/** SUCCEEDED 인데 본문이 빈 원장 불일치 — 엔진이 "" 를 저장할 수 있다. null 과 동급 결측. */
	private static final AnalysisRow MISMATCH_ROW = new AnalysisRow(
			"run-3", "KODEX 200", "069500", "XKRX", -0.031, "SUCCEEDED",
			OffsetDateTime.parse("2026-07-26T15:40:00+09:00"),
			OffsetDateTime.parse("2026-07-26T15:50:00+09:00"), "   ", null, "DRAFT",
			List.of(), 0);

	/** 무효화로 게시가 내려간 완료 런 — 실행 상태(COMPLETED)와 게시 상태(WITHDRAWN)는 별개 축. */
	private static final AnalysisRow WITHDRAWN_ROW = new AnalysisRow(
			"run-4", "TIGER 미국나스닥100", "133690", "XNAS", 0.0812, "SUCCEEDED",
			OffsetDateTime.parse("2026-07-25T15:40:00+09:00"),
			OffsetDateTime.parse("2026-07-25T15:52:00+09:00"),
			"엔진 원본 설명.", "LOW", "WITHDRAWN", List.of(), 0);

	private static final SessionOperator OPERATOR = new SessionOperator("ops@edge.io", "운영자");

	private MockMvc mvc;
	private FakeAnalysisWriteRepository writes;

	@BeforeEach
	void setUp() {
		writes = new FakeAnalysisWriteRepository(Set.of("run-1", "run-4"));
		mvc = MockMvcBuilders
				.standaloneSetup(new AnalysisController(new AnalysisService(
						new FakeAnalysisRepository(
								List.of(COMPLETED_ROW, PENDING_ROW, MISMATCH_ROW, WITHDRAWN_ROW)),
						writes)))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	/** 인증된 요청 — 필터가 보장하는 세션 운영자를 표준 세션 속성으로 싣는다. */
	private static MockHttpServletRequestBuilder authed(MockHttpServletRequestBuilder builder) {
		return builder.sessionAttr(SessionOperator.SESSION_KEY, OPERATOR);
	}

	@Test
	void 목록은_원장_행을_UI_어휘로_번역한다() throws Exception {
		mvc.perform(get("/api/v1/analyses"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.code").value("COMMON200"))
				.andExpect(jsonPath("$.result.length()").value(4))
				.andExpect(jsonPath("$.result[0].id").value("run-1"))
				.andExpect(jsonPath("$.result[0].name").value("KODEX 반도체"))
				.andExpect(jsonPath("$.result[0].market").value("KRX"))
				.andExpect(jsonPath("$.result[0].direction").value(-1))
				.andExpect(jsonPath("$.result[0].changePct").value(3.42))
				.andExpect(jsonPath("$.result[0].status").value("COMPLETED"))
				.andExpect(jsonPath("$.result[0].basisTime").value("07-27 15:40"))
				.andExpect(jsonPath("$.result[0].basisTimeAbs").value("2026-07-27 15:40 KST"))
				.andExpect(jsonPath("$.result[0].doneTime").value("2026-07-27 15:52 KST"))
				.andExpect(jsonPath("$.result[0].confidence").value("HIGH"))
				.andExpect(jsonPath("$.result[0].publicationStatus").value("PUBLISHED"))
				.andExpect(jsonPath("$.result[0].evidence.length()").value(2))
				// 표시 상한에 잘린 런 — 총 건수는 실린 2건이 아니라 57건이다(화면 문구의 근거)
				.andExpect(jsonPath("$.result[0].evidenceTotal").value(57))
				.andExpect(jsonPath("$.result[0].evidence[0].type").value("뉴스"))
				.andExpect(jsonPath("$.result[0].evidence[0].time").value("2026-07-27 09:10"))
				.andExpect(jsonPath("$.result[0].evidence[1].type").value("공시"))
				// 발행시각·제목 없는 공시 — NULL 을 시각처럼 그리지도, UI 계약(title: string)을
				// 깨지도 않는다
				.andExpect(jsonPath("$.result[0].evidence[1].time").value("—"))
				.andExpect(jsonPath("$.result[0].evidence[1].title").value("(제목 없음)"));
	}

	/** SUCCEEDED 런에 본문이 없거나 비면 원장 불일치 — 빈 완료로 숨기지 않는다(Rule 12). */
	@Test
	void 본문_없는_완료_런은_원장_불일치를_드러낸다() throws Exception {
		mvc.perform(get("/api/v1/analyses"))
				.andExpect(jsonPath("$.result[2].status").value("COMPLETED"))
				.andExpect(jsonPath("$.result[2].result")
						.value("설명 본문이 원장에 없습니다 — 완료 런의 explanation_result 가 없거나 비어 있는 원장 불일치입니다."));
	}

	@Test
	void 결과_없는_런은_상태_문구와_빈_완료시각으로_채운다() throws Exception {
		mvc.perform(get("/api/v1/analyses"))
				.andExpect(jsonPath("$.result[1].status").value("PENDING"))
				.andExpect(jsonPath("$.result[1].direction").value(1))
				.andExpect(jsonPath("$.result[1].doneTime").value("—"))
				.andExpect(jsonPath("$.result[1].confidence").doesNotExist())
				// 배지는 PENDING 으로 합쳐져도 본문은 run_status(RUNNING)의 진실을 말한다
				.andExpect(jsonPath("$.result[1].result").value("분석이 진행 중입니다."));
	}

	/**
	 * 게시 상태는 실행 상태와 별개 축 — 무효화로 WITHDRAWN 이 돼도 실행 상태(COMPLETED)·
	 * 완료시각·본문은 그대로다(게시 수명주기가 실행 이력을 덮지 않는다).
	 */
	@Test
	void 게시_상태는_실행_상태와_별개_축으로_노출된다() throws Exception {
		mvc.perform(get("/api/v1/analyses"))
				.andExpect(jsonPath("$.result[3].id").value("run-4"))
				.andExpect(jsonPath("$.result[3].status").value("COMPLETED"))
				.andExpect(jsonPath("$.result[3].publicationStatus").value("WITHDRAWN"))
				.andExpect(jsonPath("$.result[3].doneTime").value("2026-07-25 15:52 KST"))
				.andExpect(jsonPath("$.result[3].confidence").value("LOW"))
				.andExpect(jsonPath("$.result[3].result").value("엔진 원본 설명."));
	}

	@Test
	void 무효화는_작업자_사유를_저장계층까지_흘린다() throws Exception {
		mvc.perform(authed(post("/api/v1/analyses/run-1/invalidate"))
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"reason\":\"전제 데이터 정정\"}"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true));

		assertThat(writes.calls()).singleElement().satisfies(call -> {
			assertThat(call.action()).isEqualTo("INVALIDATE");
			assertThat(call.runId()).isEqualTo("run-1");
			assertThat(call.reason()).isEqualTo("전제 데이터 정정");
			assertThat(call.actor()).isEqualTo(OPERATOR);
		});
	}

	@Test
	void 사유_없는_무효화는_400이다() throws Exception {
		mvc.perform(authed(post("/api/v1/analyses/run-1/invalidate")))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("ADMN4001"));
		assertThat(writes.calls()).isEmpty();
	}

	@Test
	void 없는_런의_무효화는_404다() throws Exception {
		mvc.perform(authed(post("/api/v1/analyses/unknown/invalidate"))
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"reason\":\"y\"}"))
				.andExpect(status().isNotFound())
				.andExpect(jsonPath("$.code").value("ADMN4041"));
	}

	/**
	 * 은퇴한 3종 표면(정정·제외·복원)은 라우트 자체가 없다 — 재도입되면 이 테스트가 깨진다
	 * (ALPHA-737 은퇴 결정의 부정 단언, Rule 9).
	 */
	@Test
	void 은퇴한_정정_제외_복원_표면은_라우트가_없다() throws Exception {
		mvc.perform(authed(patch("/api/v1/analyses/run-1/result"))
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"result\":\"x\",\"reason\":\"y\"}"))
				.andExpect(status().isNotFound());
		mvc.perform(authed(post("/api/v1/analyses/run-1/exclude"))
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"reason\":\"y\"}"))
				.andExpect(status().isNotFound());
		mvc.perform(authed(post("/api/v1/analyses/run-1/restore")))
				.andExpect(status().isNotFound());
		assertThat(writes.calls()).isEmpty();
	}

	/** 게시 상태가 아닌 런(DRAFT·이미 무효화)의 무효화는 409 — 대상 부재(404)와 다른 사실이다. */
	@Test
	void 미게시_런의_무효화는_409다() throws Exception {
		writes.markUnpublished("run-4");
		mvc.perform(authed(post("/api/v1/analyses/run-4/invalidate"))
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"reason\":\"y\"}"))
				.andExpect(status().isConflict())
				.andExpect(jsonPath("$.code").value("ADMN4090"));
		assertThat(writes.calls()).isEmpty();
	}
}
