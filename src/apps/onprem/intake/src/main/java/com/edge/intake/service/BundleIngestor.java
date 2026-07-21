package com.edge.intake.service;

import com.edge.intake.client.SyncAgentClient.PulledBundle;
import com.edge.intake.repository.ReceivedBundleRepository;
import com.edge.intake.repository.SyncStateRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/**
 * 번들 1건의 적재 트랜잭션 — received_bundle insert + sync_state 전진을 한 단위로 commit.
 * 본문은 파싱하지 않는다(상태 분기는 Screening 몫) — 봉투의 cursor_from·cursor_to 두
 * 필드만 읽어 저장 키·전진값으로 쓴다. 중복 수신은 무해(cursor dedup)하되 cursor 는
 * 전진한다 — at-least-once 재-Pull 수렴(sync-protocol.md).
 */
@Service
public class BundleIngestor {

	private static final Logger log = LoggerFactory.getLogger(BundleIngestor.class);

	private final ReceivedBundleRepository receivedBundleRepository;
	private final SyncStateRepository syncStateRepository;
	private final ObjectMapper objectMapper = new ObjectMapper();

	public BundleIngestor(ReceivedBundleRepository receivedBundleRepository,
			SyncStateRepository syncStateRepository) {
		this.receivedBundleRepository = receivedBundleRepository;
		this.syncStateRepository = syncStateRepository;
	}

	/**
	 * @return 전진한 cursor(cursor_to). 봉투 형상 위반·cursor 역행은 즉시 실패(fail-loud) —
	 * 저장도 전진도 하지 않는다. committed cursor 는 단조 증가만 허용한다(권위 재개점이
	 * 후퇴하면 소비 완료 번들을 재수신하고, 제자리면 같은 번들을 영원히 반복한다).
	 */
	@Transactional
	public long ingest(PulledBundle bundle) {
		JsonNode envelope = objectMapper.readTree(bundle.body());
		JsonNode from = envelope.path("cursor_from");
		JsonNode to = envelope.path("cursor_to");
		if (!from.isIntegralNumber() || !to.isIntegralNumber()) {
			throw new IllegalStateException("번들 봉투에 cursor_from/cursor_to 가 없다 — 계약 위반");
		}
		long cursorFrom = from.asLong();
		long cursorTo = to.asLong();
		if (cursorFrom <= 0 || cursorTo < cursorFrom) {
			throw new IllegalStateException(
					"번들 cursor 범위가 비정상이다 (from=" + cursorFrom + ", to=" + cursorTo + ") — 계약 위반");
		}
		long committed = syncStateRepository.lastCursor();
		if (cursorTo <= committed) {
			throw new IllegalStateException(
					"번들 cursor_to=" + cursorTo + " 가 committed=" + committed + " 이하 — 후퇴 금지, 적재하지 않는다");
		}

		boolean inserted = receivedBundleRepository.save(
				cursorFrom, cursorTo, bundle.checksum(), bundle.body());
		syncStateRepository.advance(cursorTo);
		log.info("bundle ingested cursor_from={} cursor_to={} inserted={}", cursorFrom, cursorTo, inserted);
		return cursorTo;
	}
}
