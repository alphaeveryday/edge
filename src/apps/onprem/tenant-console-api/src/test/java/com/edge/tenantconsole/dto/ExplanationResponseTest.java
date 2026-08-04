package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.model.Explanation;
import org.junit.jupiter.api.Test;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.OffsetDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * explanations 응답의 UI 계약 번역을 검증한다(ALPHA-607). WHY: UI 는 이 필드명
 * (camelCase·`final`)과 어휘(근거 공시/뉴스)로 그대로 렌더한다 — 직렬화가 finalText 로
 * 새거나, 축소 계약에서 빠진 market/direction/changePct 가 되살아나면 화면이 깨진다.
 */
class ExplanationResponseTest {

	private final ObjectMapper mapper = new ObjectMapper();

	private static Explanation sample(String reviewReason) {
		return new Explanation("expr_LOCAL01", "삼성전자", "005930", "REVIEW_REQUIRED", "HIGH",
				reviewReason, OffsetDateTime.parse("2026-07-11T10:42:00+09:00"),
				OffsetDateTime.parse("2026-07-11T16:00:00+09:00"), true,
				List.of(new Explanation.Evidence("DISCLOSURE", "3분기 잠정 실적 공시", "KIND",
								OffsetDateTime.parse("2026-07-11T10:31:00+09:00"),
								"https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260711000001"),
						new Explanation.Evidence("NEWS", null, "연합인포맥스", null, null)),
				"원본 문구", "최종 문구");
	}

	@Test
	void 응답은_UI_계약_형상이다() {
		JsonNode json = mapper.valueToTree(ExplanationResponse.from(sample("ASSERTIVE")));

		assertThat(json.get("id").asString()).isEqualTo("expr_LOCAL01");
		assertThat(json.get("name").asString()).isEqualTo("삼성전자");
		assertThat(json.get("code").asString()).isEqualTo("005930");
		assertThat(json.get("status").asString()).isEqualTo("REVIEW_REQUIRED");
		assertThat(json.get("risk").asString()).isEqualTo("HIGH");
		assertThat(json.get("reviewReason").asString()).isEqualTo("ASSERTIVE");
		// `final` 은 예약어 우회(finalText)로 새면 안 된다
		assertThat(json.get("final").asString()).isEqualTo("최종 문구");
		assertThat(json.has("finalText")).isFalse();
		assertThat(json.get("original").asString()).isEqualTo("원본 문구");
		assertThat(json.get("receivedAt").asString()).isEqualTo("2026-07-11 10:42 KST");
		assertThat(json.get("receivedRelative").asString()).endsWith("전");
		// 기준시각·노출 head(ALPHA-744) — UI 는 이 두 필드로 다스냅샷 공존을 판별한다
		assertThat(json.get("explanationAsOf").asString()).isEqualTo("2026-07-11 16:00 KST");
		assertThat(json.get("serving").asBoolean()).isTrue();
	}

	@Test
	void 축소_계약_필드는_응답에_없다() {
		// WHY: market·direction·changePct 는 온프렘 원장에 없어(ALPHA-497 이연) 제거됐다 —
		// 되살아나면 UI 가 가짜값을 그린다.
		JsonNode json = mapper.valueToTree(ExplanationResponse.from(sample("ASSERTIVE")));

		assertThat(json.has("market")).isFalse();
		assertThat(json.has("direction")).isFalse();
		assertThat(json.has("changePct")).isFalse();
	}

	@Test
	void 근거_kind_는_공시_뉴스로_번역되고_결측은_폴백한다() {
		JsonNode evidence = mapper.valueToTree(ExplanationResponse.from(sample("ASSERTIVE")))
				.get("evidence");

		assertThat(evidence.get(0).get("type").asString()).isEqualTo("공시");
		assertThat(evidence.get(0).get("time").asString()).isEqualTo("2026-07-11 10:31");
		// 원문 링크(ALPHA-739) — 있으면 그대로, 없으면 키 생략(NON_NULL — UI 는 링크 미표시)
		assertThat(evidence.get(0).get("sourceUri").asString())
				.isEqualTo("https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260711000001");
		assertThat(evidence.get(1).get("type").asString()).isEqualTo("뉴스");
		// 제목·발행시각 없는 근거 — UI 계약(string)을 깨지 않게 폴백한다
		assertThat(evidence.get(1).get("title").asString()).isEqualTo("(제목 없음)");
		assertThat(evidence.get(1).get("time").asString()).isEqualTo("—");
		assertThat(evidence.get(1).has("sourceUri")).isFalse();
	}

	@Test
	void 사유_없는_항목은_reviewReason_키를_생략한다() {
		// WHY: UI optional 계약 — 자동 제공 등 사유 없는 상태는 필드 자체가 없어야 한다
		JsonNode json = mapper.valueToTree(ExplanationResponse.from(sample(null)));

		assertThat(json.has("reviewReason")).isFalse();
	}
}
