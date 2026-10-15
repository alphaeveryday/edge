package com.edge.common.apipayload;

import com.edge.common.apipayload.code.status.SuccessStatus;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 와이어 계약: 직렬화 키는 정확히 {isSuccess, code, message[, result]} — 순서 포함.
 * WHY(Rule 9): 종전 class+Lombok 구현은 필드(@JsonProperty "isSuccess")와 is-게터(암묵명
 * "success")가 별개 프로퍼티로 갈라져 전 모듈 응답에 중복 키 "success" 가 실렸다(ALPHA-605).
 * 존재 단언(jsonPath 류)만으로는 여분 키를 못 잡으므로, 키 집합을 전수 비교해 그 회귀를 차단한다.
 * 소비자 런타임과 동일한 Jackson 3(tools.jackson)로 검증한다.
 */
class ApiResponseSerializationTest {

	private final ObjectMapper objectMapper = new ObjectMapper();

	@Test
	void 성공_응답의_키는_정확히_isSuccess_code_message_result_순이다() {
		JsonNode node = objectMapper.readTree(objectMapper.writeValueAsString(ApiResponse.onSuccess("data")));

		assertThat(fieldNames(node)).containsExactly("isSuccess", "code", "message", "result");
		assertThat(node.get("isSuccess").asBoolean()).isTrue();
		assertThat(node.get("code").asString()).isEqualTo(SuccessStatus.OK.getCode());
		assertThat(node.get("result").asString()).isEqualTo("data");
	}

	@Test
	void result가_null이면_필드가_생략되고_여분_키는_없다() {
		// WHY: "result 부재 = 데이터 없음" 이 소비자 판별 계약이다(ADR-0042 — sync 신규 없음 등).
		// null 명시가 실리면 부재/명시를 엄격 구분하는 소비자(intake)가 계약 위반으로 거부한다.
		JsonNode node = objectMapper.readTree(objectMapper.writeValueAsString(ApiResponse.onSuccess(null)));

		assertThat(fieldNames(node)).containsExactly("isSuccess", "code", "message");
	}

	@Test
	void 실패_응답도_같은_키_집합이며_isSuccess는_false다() {
		JsonNode node = objectMapper.readTree(
				objectMapper.writeValueAsString(ApiResponse.onFailure("X4000", "실패", null)));

		assertThat(fieldNames(node)).containsExactly("isSuccess", "code", "message");
		assertThat(node.get("isSuccess").asBoolean()).isFalse();
	}

	private static List<String> fieldNames(JsonNode node) {
		List<String> names = new ArrayList<>();
		node.propertyNames().forEach(names::add);
		return names;
	}
}
