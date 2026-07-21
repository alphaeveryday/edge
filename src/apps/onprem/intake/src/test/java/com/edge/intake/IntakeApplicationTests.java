package com.edge.intake;

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;

/** 폴러의 첫 실행을 뒤로 미뤄(초기 지연) DB 없이 컨텍스트 로딩만 검증한다. */
@SpringBootTest
@TestPropertySource(properties = "intake.initial-delay-ms=600000")
class IntakeApplicationTests {

	@Test
	void contextLoads() {
	}
}
