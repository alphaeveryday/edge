package com.edge.intake;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * Intake — 내부망 배치. Sync Agent 가 검증한 번들을 넘겨받아 Raw Event Store 에
 * 멱등 적재한다. 외부 통신이 없다(ADR-0036). committed cursor 의 권위는 이 모듈이다
 * (sync-protocol.md — Pull 재개점 = Intake 의 durable cursor).
 */
@SpringBootApplication
@EnableScheduling
public class IntakeApplication {

	public static void main(String[] args) {
		SpringApplication.run(IntakeApplication.class, args);
	}
}
