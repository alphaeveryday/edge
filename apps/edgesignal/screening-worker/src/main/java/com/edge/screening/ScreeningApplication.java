package com.edge.screening;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * Screening Worker — 반입 파이프라인의 점검 단계(state-machine.md). Raw Event Store 의
 * 미점검 번들을 파싱해 analysis_item 상태를 분기하고 노출 가능분을 publication 으로
 * 게시한다. walking skeleton 정책: NEW 는 무조건 통과(AUTO_PUBLISHED) — 정책 룰 엔진은 후속.
 */
@SpringBootApplication
@EnableScheduling
public class ScreeningApplication {

	public static void main(String[] args) {
		SpringApplication.run(ScreeningApplication.class, args);
	}
}
