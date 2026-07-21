package com.edge.publication.exposure;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

/**
 * Exposure Log 기록 — 조회(200 응답) = 노출 간주(ADR-0013). 응답한 문구 스냅샷·고객 해시·
 * 채널을 온프렘 exposure_log 테이블에 남겨 민원·감사 시 재현한다(온프렘 거주 한정 —
 * data-residency.md). summary_snapshot 은 스키마가 NOT NULL 로 강제한다.
 */
@Component
public class ExposureLogRecorder {

	private static final Logger log = LoggerFactory.getLogger(ExposureLogRecorder.class);

	private static final String INSERT_SQL = """
			INSERT INTO exposure_log (publication_id, customer_hash, channel, summary_snapshot)
			VALUES (?, ?, ?, ?)
			""";

	private final JdbcTemplate jdbc;

	public ExposureLogRecorder(JdbcTemplate jdbc) {
		this.jdbc = jdbc;
	}

	public void record(long publicationId, String ticker, String summarySnapshot,
			String customerHash, String channel) {
		jdbc.update(INSERT_SQL, publicationId, customerHash, channel, summarySnapshot);
		log.info("exposure recorded publication_id={} ticker={} channel={} customer_hash={}",
				publicationId, ticker, channel, customerHash);
	}
}
