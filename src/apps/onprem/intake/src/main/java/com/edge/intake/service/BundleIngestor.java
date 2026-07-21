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
 * 필드만 읽어 저장 키·전진값으로 쓴다. at-least-once 재-Pull 수렴(sync-protocol.md):
 * 정확한 중복(소비 완료 범위)은 committed 이하 가드가 거르고, 부분 겹침(conflict-skip
 * 인데 범위가 committed 초과)은 전진 없이 실패시켜 유실을 막는다.
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
		if (!inserted) {
			// cursor_from 이 이미 저장돼 있는데 범위(cursor_to)는 committed 를 넘는다 —
			// 부분 겹침 번들(경쟁 인스턴스·지연 응답). 여기서 전진하면 겹치지 않은 구간이
			// 영영 저장되지 않으므로(at-least-once 위반) 전진 없이 실패한다. 다음 틱이
			// 진짜 committed 에서 재-Pull 하면 겹침 없는 범위를 받는다.
			throw new IllegalStateException(
					"부분 겹침 번들 (from=" + cursorFrom + ", to=" + cursorTo + ", committed=" + committed
							+ ") — 전진하지 않는다");
		}
		syncStateRepository.advance(cursorTo);
		log.info("bundle ingested cursor_from={} cursor_to={}", cursorFrom, cursorTo);
		return cursorTo;
	}
}
