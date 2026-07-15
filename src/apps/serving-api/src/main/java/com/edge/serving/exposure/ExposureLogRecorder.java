package com.edge.serving.exposure;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * Exposure Log 기록 — 조회(200 응답) = 노출 간주(ADR-0013). 응답한 문구 스냅샷·고객 해시·
 * 채널·시각을 남겨 민원·감사 시 재현한다.
 * 기본형: 인메모리 + 구조화 로그. 온프렘 exposure_log 테이블(도메인 마이그레이션) 확정 시
 * 이 클래스를 DB 기록으로 직접 재작성한다 — 쓰기 경로 설계(유실 불가·저지연)는 오너 영역.
 */
@Component
public class ExposureLogRecorder {

	private static final Logger log = LoggerFactory.getLogger(ExposureLogRecorder.class);

	public record ExposureRecord(
			String publicationId,
			String ticker,
			String summarySnapshot,
			String customerHash,
			String channel,
			Instant exposedAt
	) {
	}

	private final List<ExposureRecord> records = new CopyOnWriteArrayList<>();

	public void record(String publicationId, String ticker, String summarySnapshot,
			String customerHash, String channel) {
		ExposureRecord r = new ExposureRecord(publicationId, ticker, summarySnapshot,
				customerHash, channel, Instant.now());
		records.add(r);
		log.info("exposure recorded publication_id={} ticker={} channel={} customer_hash={}",
				publicationId, ticker, channel, customerHash);
	}

	/** 데모·테스트 확인용 읽기 뷰. */
	public List<ExposureRecord> records() {
		return Collections.unmodifiableList(records);
	}
}
