package com.edge.intake.service;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.jupiter.api.Assertions.assertEquals;

import com.edge.intake.client.SyncAgentClient;
import com.edge.intake.dto.FetchedBundle;
import com.edge.intake.repository.ReceivedBundleStore;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * WHY: committed cursor 는 적재 성공 뒤에만 전진해야 한다(ADR-0036) — 실패 시 전진하면 그 번들을
 * 영영 건너뛰어(skip) 유실된다. 드레인은 204(소진)에서 멈춘다.
 */
class IntakeServiceTest {

	/** 인메모리 cursor·적재 카운터로 실제 DB 없이 전진 규율을 검증. */
	static class FakeStore extends ReceivedBundleStore {
		long cursor = 0L;
		int stores = 0;

		FakeStore() {
			super(null);
		}

		@Override
		public long lastCursor() {
			return cursor;
		}

		@Override
		public void store(long cursorFrom, long cursorTo, String checksum, byte[] body) {
			stores++;
			cursor = Math.max(cursor, cursorTo);
		}
	}

	private static byte[] bundle(long from, long to) {
		return ("{\"cursor_from\":" + from + ",\"cursor_to\":" + to + "}").getBytes(UTF_8);
	}

	@Test
	@DisplayName("드레인은 204까지 번들을 적재하고 cursor 를 전진시킨다")
	void 드레인_적재_전진() {
		FakeStore store = new FakeStore();
		SyncAgentClient client = new SyncAgentClient("http://sync-agent-stub", 3000, 10000) {
			@Override
			public FetchedBundle fetch(long after) {
				if (after == 0L) {
					return FetchedBundle.of(bundle(1, 3), "sha256=a");
				}
				if (after == 3L) {
					return FetchedBundle.of(bundle(4, 5), "sha256=b");
				}
				return FetchedBundle.noContent();
			}
		};

		new IntakeService(client, store).drain();

		assertEquals(2, store.stores);
		assertEquals(5L, store.cursor);
	}

	@Test
	@DisplayName("sync-agent 실패 시 cursor 를 전진시키지 않는다(유실 방지) — 예외를 삼키되 로그로 드러낸다")
	void fetch_실패_미전진() {
		FakeStore store = new FakeStore();
		SyncAgentClient client = new SyncAgentClient("http://sync-agent-stub", 3000, 10000) {
			@Override
			public FetchedBundle fetch(long after) {
				throw new IllegalStateException("sync-agent 502");
			}
		};

		new IntakeService(client, store).drain(); // 스케줄러가 죽지 않게 삼킴 — 던지지 않음

		assertEquals(0, store.stores);
		assertEquals(0L, store.cursor);
	}
}
