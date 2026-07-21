package com.edge.intake.poller;

import com.edge.intake.service.IntakeService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/** 고정 지연 폴링. 예외를 삼켜 스케줄러가 죽지 않게 하되, 반드시 로그로 드러낸다(Rule 12). */
@Component
public class IntakePoller {

	private static final Logger log = LoggerFactory.getLogger(IntakePoller.class);

	private final IntakeService service;

	public IntakePoller(IntakeService service) {
		this.service = service;
	}

	@Scheduled(fixedDelayString = "${intake.poll-ms:5000}", initialDelayString = "${intake.initial-delay-ms:0}")
	public void poll() {
		try {
			service.drain();
		} catch (RuntimeException e) {
			log.error("intake 폴링 실패 — 다음 주기 재시도", e);
		}
	}
}
