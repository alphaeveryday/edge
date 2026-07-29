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
 * UI 계약(super-admin-ui analyses 도메인) 검증: 원장 어휘가 UI 어휘로 번역되고(ALPHA-601), 쓰기
 * (정정·제외·복원)가 원장 전이로 바뀐 뒤(ALPHA-602)의 계약을 고정한다 — <b>사유 필수</b>,
 * 작업자(세션 운영자)·사유가 저장 계층까지 흐르고, 제외 오버레이가 상태 배지만 EXCLUDED 로
 * 바꾸되 완료시각·정정 플래그는 원상태 기준으로 유지된다.
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
			"반도체 업황 회복 기대가 확산되며 상승.", "HIGH", false, false, null,
			List.of(new EvidenceRow("NEWS", "반도체 수출 반등", "BIGKINDS",
							OffsetDateTime.parse("2026-07-27T09:10:00+09:00")),
					new EvidenceRow("DISCLOSURE", null, "DART", null)),
			57);

	/** 결과 행이 아직 없는 런 — summary·confidence 가 null 로 온다. */
	private static final AnalysisRow PENDING_ROW = new AnalysisRow(
			"run-2", "TIGER 2차전지", "305540", "XKRX", 0.0518, "RUNNING",
			OffsetDateTime.parse("2026-07-27T15:40:00+09:00"), null, null, null, false, false, null,
			List.of(), 0);

	/** SUCCEEDED 인데 본문이 빈 원장 불일치 — 엔진이 "" 를 저장할 수 있다. null 과 동급 결측. */
	private static final AnalysisRow MISMATCH_ROW = new AnalysisRow(
			"run-3", "KODEX 200", "069500", "XKRX", -0.031, "SUCCEEDED",
			OffsetDateTime.parse("2026-07-26T15:40:00+09:00"),
			OffsetDateTime.parse("2026-07-26T15:50:00+09:00"), "   ", null, false, false, null,
			List.of(), 0);

	/**
	 * 제외·정정 오버레이가 선 완료 런 — 상태는 EXCLUDED, 완료시각은 원상태 기준, 본문은 정정본
	 * (원장 원본이 아니라 admin_activity_log 의 정정 문구).
	 */
	private static final AnalysisRow EXCLUDED_ROW = new AnalysisRow(
			"run-4", "TIGER 미국나스닥100", "133690", "XNAS", 0.0812, "SUCCEEDED",
			OffsetDateTime.parse("2026-07-25T15:40:00+09:00"),
			OffsetDateTime.parse("2026-07-25T15:52:00+09:00"),
			"엔진 원본 설명.", "LOW", true, true, "운영자가 정정한 설명 본문.", List.of(), 0);

	private static final SessionOperator OPERATOR = new SessionOperator("ops@edge.io", "운영자");

	private MockMvc mvc;
	private FakeAnalysisWriteRepository writes;

	@BeforeEach
	void setUp() {
		writes = new FakeAnalysisWriteRepository(Set.of("run-1", "run-4"));
		mvc = MockMvcBuilders
				.standaloneSetup(new AnalysisController(new AnalysisService(
						new FakeAnalysisRepository(
								List.of(COMPLETED_ROW, PENDING_ROW, MISMATCH_ROW, EXCLUDED_ROW)),
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
				.andExpect(jsonPath("$.result[0].corrected").value(false))
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
	 * 제외 오버레이는 상태 배지만 EXCLUDED 로 바꾼다 — 완료시각·정정 플래그·본문은 원래 run_status
	 * 기준을 유지한다(제외된 완료 런이 완료 시각을 잃지 않는다).
	 */
	@Test
	void 제외_오버레이는_상태만_바꾸고_완료시각은_유지한다() throws Exception {
		mvc.perform(get("/api/v1/analyses"))
				.andExpect(jsonPath("$.result[3].id").value("run-4"))
				.andExpect(jsonPath("$.result[3].status").value("EXCLUDED"))
				.andExpect(jsonPath("$.result[3].corrected").value(true))
				.andExpect(jsonPath("$.result[3].doneTime").value("2026-07-25 15:52 KST"))
				.andExpect(jsonPath("$.result[3].confidence").value("LOW"))
				// 본문은 원장 원본이 아니라 정정본을 낸다(원본 explanation_result 는 불변)
				.andExpect(jsonPath("$.result[3].result").value("운영자가 정정한 설명 본문."));
	}

	@Test
	void 정정은_원장_전이로_바뀌어_작업자_사유를_저장계층까지_흘린다() throws Exception {
		mvc.perform(authed(patch("/api/v1/analyses/run-1/result"))
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"result\":\"정정된 결과 본문\",\"reason\":\"근거 재확인\"}"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true));

		assertThat(writes.calls()).singleElement().satisfies(call -> {
			assertThat(call.action()).isEqualTo("CORRECT");
			assertThat(call.runId()).isEqualTo("run-1");
			assertThat(call.result()).isEqualTo("정정된 결과 본문");
			assertThat(call.reason()).isEqualTo("근거 재확인");
			assertThat(call.actor()).isEqualTo(OPERATOR);
		});
	}

	@Test
	void 제외_복원도_원장_전이로_바뀐다() throws Exception {
		mvc.perform(authed(post("/api/v1/analyses/run-4/exclude"))
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"reason\":\"근거 신뢰도 미달\"}"))
				.andExpect(status().isOk());
		mvc.perform(authed(post("/api/v1/analyses/run-4/restore")))
				.andExpect(status().isOk());

		assertThat(writes.calls()).extracting("action").containsExactly("EXCLUDE", "RESTORE");
		assertThat(writes.calls().get(0).reason()).isEqualTo("근거 신뢰도 미달");
		assertThat(writes.calls().get(0).actor()).isEqualTo(OPERATOR);
	}

	@Test
	void 없는_런의_쓰기는_404다() throws Exception {
		mvc.perform(authed(patch("/api/v1/analyses/unknown/result"))
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"result\":\"x\",\"reason\":\"y\"}"))
				.andExpect(status().isNotFound())
				.andExpect(jsonPath("$.code").value("ADMN4040"));
		mvc.perform(authed(post("/api/v1/analyses/unknown/exclude"))
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"reason\":\"y\"}"))
				.andExpect(status().isNotFound());
		mvc.perform(authed(post("/api/v1/analyses/unknown/restore")))
				.andExpect(status().isNotFound());
	}

	@Test
	void 빈_정정_결과는_400이다() throws Exception {
		mvc.perform(authed(patch("/api/v1/analyses/run-1/result"))
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"result\":\" \",\"reason\":\"근거 재확인\"}"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("ADMN4001"));
	}

	@Test
	void 사유_없는_정정은_400이다() throws Exception {
		mvc.perform(authed(patch("/api/v1/analyses/run-1/result"))
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"result\":\"정정 본문\"}"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("ADMN4001"));
	}

	@Test
	void 사유_없는_제외는_400이다() throws Exception {
		mvc.perform(authed(post("/api/v1/analyses/run-4/exclude"))
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"reason\":\" \"}"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("ADMN4001"));
		assertThat(writes.calls()).isEmpty();
	}

	/** 복원 사유는 선택 — 본문 없이도 통과한다(정정/무효화만 사유 필수). */
	@Test
	void 복원은_사유_없이도_통과한다() throws Exception {
		mvc.perform(authed(post("/api/v1/analyses/run-4/restore")))
				.andExpect(status().isOk());
		assertThat(writes.calls()).singleElement()
				.satisfies(call -> assertThat(call.reason()).isNull());
	}
}
