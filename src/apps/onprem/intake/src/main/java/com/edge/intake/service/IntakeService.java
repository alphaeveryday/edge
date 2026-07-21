package com.edge.intake.service;

import com.edge.intake.client.SyncAgentClient;
import com.edge.intake.dto.FetchedBundle;
import com.edge.intake.envelope.BundleEnvelope;
import com.edge.intake.repository.ReceivedBundleStore;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * 폴링 1회분 드레인: committed cursor 부터 새 번들이 없을 때(204)까지 받아 적재한다.
 * cursor 는 적재 커밋 뒤에만 전진하므로(store 내부 트랜잭션) 전송·적재 실패 시 유실 없이 재시도된다.
 */
@Service
public class IntakeService {

	private static final Logger log = LoggerFactory.getLogger(IntakeService.class);
	private static final int MAX_DRAIN = 1000; // 한 폴링에서의 안전 상한(무한 루프 방지)

	private final SyncAgentClient client;
	private final ReceivedBundleStore store;

	public IntakeService(SyncAgentClient client, ReceivedBundleStore store) {
		this.client = client;
		this.store = store;
	}

	public void drain() {
		for (int i = 0; i < MAX_DRAIN; i++) {
			long after = store.lastCursor();

			FetchedBundle bundle;
			try {
				bundle = client.fetch(after);
			} catch (RuntimeException e) {
				// sync-agent 실패 — cursor 미전진, 다음 폴링 재시도(Rule 12: 조용히 삼키지 않고 로그).
				log.warn("sync-agent fetch 실패 (after={}) — cursor 미전진", after, e);
				return;
			}

			if (bundle.empty()) {
				return; // 소진 — 새 이벤트 없음
			}

			BundleEnvelope.CursorRange range = BundleEnvelope.cursorRange(bundle.body());
			store.store(range.from(), range.to(), bundle.checksum(), bundle.body());

			if (store.lastCursor() <= after) {
				// committed cursor(=MAX(cursor_to))가 전진하지 않음 — 경합·중복 번들.
				// 같은 after 로 무한 재요청하지 않도록 이번 폴링은 중단(다음 주기 재개).
				log.warn("cursor 미전진 (after={}, range={}..{}) — 드레인 중단", after, range.from(), range.to());
				return;
			}
			log.info("번들 적재 cursor {}..{} — sync_state 전진", range.from(), range.to());
		}
		log.warn("드레인 상한 {} 도달 — 다음 폴링에서 계속", MAX_DRAIN);
	}
}
