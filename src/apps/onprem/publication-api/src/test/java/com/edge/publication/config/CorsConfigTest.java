package com.edge.publication.config;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * CORS 설정 게이트(ADR-0053 결정 5) — WHY: ① 와일드카드가 허용되면 "위젯 오리진만 등록"
 * 계약이 설정 실수 하나로 무너지므로 기동 실패로 드러나야 하고(Rule 12), ② 기본값(빈
 * 프로퍼티)은 Spring 이 [""] 로 바인딩하는 형상이라 이를 미등록(no-op)으로 정규화해야
 * 기본형(동일 오리진 프록시) 배치가 CORS 없이 기동된다.
 */
class CorsConfigTest {

	@Test
	void 와일드카드_오리진은_기동을_거부한다() {
		assertThatThrownBy(() -> new CorsConfig(List.of("*")))
				.isInstanceOf(IllegalArgumentException.class);
		assertThatThrownBy(() -> new CorsConfig(List.of("https://widget.example.com", "https://*.example.com")))
				.isInstanceOf(IllegalArgumentException.class);
	}

	@Test
	void 빈_값과_공백_항목은_미등록으로_정규화된다() {
		// @Value 빈 프로퍼티의 실제 바인딩 형상([""])과 공백 항목 — 예외 없이 no-op 이어야 한다.
		assertThatCode(() -> new CorsConfig(List.of(""))).doesNotThrowAnyException();
		assertThatCode(() -> new CorsConfig(List.of(" ", "https://widget.example.com")))
				.doesNotThrowAnyException();
	}
}
