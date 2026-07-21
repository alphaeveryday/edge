package com.edge.syncagent;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Sync Agent — DMZ 배치. Tenant Sync API 를 outbound Pull 하고 번들 무결성(SHA-256)을
 * 검증해 내부망(Intake)에 무변형 전달한다. 내부망 DB 에 직접 접근하지 않는다(ADR-0036).
 */
@SpringBootApplication
public class SyncAgentApplication {

	public static void main(String[] args) {
		SpringApplication.run(SyncAgentApplication.class, args);
	}
}
