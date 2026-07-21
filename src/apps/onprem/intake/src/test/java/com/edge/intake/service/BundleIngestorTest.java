package com.edge.intake.service;

import com.edge.intake.client.SyncAgentClient.PulledBundle;
import com.edge.intake.repository.ReceivedBundleRepository;
import com.edge.intake.repository.SyncStateRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.function.Executable;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;

/**
 * 적재 계약을 검증한다 — sync-protocol.md 의 전달 보장: ① 저장과 cursor 전진이 한 단위
 * ② 중복 수신은 무해하되 cursor 는 전진(at-least-once 수렴) ③ 봉투 형상 위반은 저장 없이
 * 실패(fail-loud — 오염 데이터가 Raw Event Store 에 남지 않는다).
 */
class BundleIngestorTest {

	private static final class RecordingBundleRepo extends ReceivedBundleRepository {
		record Saved(long cursorFrom, long cursorTo, String checksum) {
		}

		final List<Saved> saved = new ArrayList<>();
		boolean insertResult = true;

		RecordingBundleRepo() {
			super(null);
		}

		@Override
		public boolean save(long cursorFrom, long cursorTo, String checksum, byte[] body) {
			saved.add(new Saved(cursorFrom, cursorTo, checksum));
			return insertResult;
		}
	}

	private static final class RecordingStateRepo extends SyncStateRepository {
		final List<Long> advanced = new ArrayList<>();
		long committed = 0;

		RecordingStateRepo() {
			super(null);
		}

		@Override
		public long lastCursor() {
			return committed;
		}

		@Override
		public void advance(long cursorTo) {
			advanced.add(cursorTo);
		}
	}

	private static PulledBundle bundle(String json) {
		return new PulledBundle(json.getBytes(StandardCharsets.UTF_8), "sha256=stub");
	}

	@Test
	void 정상_번들은_저장되고_cursor가_cursor_to로_전진한다() {
		RecordingBundleRepo bundles = new RecordingBundleRepo();
		RecordingStateRepo state = new RecordingStateRepo();
		long advanced = new BundleIngestor(bundles, state)
				.ingest(bundle("{\"cursor_from\":1,\"cursor_to\":3,\"entries\":[]}"));

		assertThat(advanced).isEqualTo(3);
		assertThat(bundles.saved).containsExactly(new RecordingBundleRepo.Saved(1, 3, "sha256=stub"));
		assertThat(state.advanced).containsExactly(3L);
	}

	@Test
	void 부분_겹침_번들은_전진하지_않는다() {
		// WHY: cursor_from 이 저장돼 있는데(conflict-skip) 범위가 committed 를 넘는 번들에서
		// 전진하면 겹치지 않은 구간이 영영 저장되지 않는다(at-least-once 위반 — 경쟁
		// 인스턴스·지연 응답 경로). 정확한 중복(범위 전체 소비 완료)은 committed 이하
		// 가드가 먼저 거른다 — save 까지 온 conflict 는 곧 부분 겹침이다.
		RecordingBundleRepo bundles = new RecordingBundleRepo();
		bundles.insertResult = false; // ON CONFLICT DO NOTHING 경로
		RecordingStateRepo state = new RecordingStateRepo();
		state.committed = 3;
		Executable call = () -> new BundleIngestor(bundles, state)
				.ingest(bundle("{\"cursor_from\":1,\"cursor_to\":100}"));

		assertThrows(IllegalStateException.class, call);
		assertThat(state.advanced).isEmpty();
	}

	@Test
	void 봉투에_cursor가_없으면_저장_없이_실패한다() {
		RecordingBundleRepo bundles = new RecordingBundleRepo();
		RecordingStateRepo state = new RecordingStateRepo();
		Executable call = () -> new BundleIngestor(bundles, state).ingest(bundle("{\"entries\":[]}"));

		assertThrows(IllegalStateException.class, call);
		assertThat(bundles.saved).isEmpty();
		assertThat(state.advanced).isEmpty();
	}

	@Test
	void committed_이하의_번들은_저장도_전진도_하지_않는다() {
		// WHY: committed cursor 는 권위 재개점(sync-protocol.md) — 후퇴하면 소비 완료 번들을
		// 재수신하고, 제자리(cursor_to == committed)면 같은 번들을 영원히 반복한다.
		RecordingBundleRepo bundles = new RecordingBundleRepo();
		RecordingStateRepo state = new RecordingStateRepo();
		state.committed = 10;
		Executable call = () -> new BundleIngestor(bundles, state)
				.ingest(bundle("{\"cursor_from\":1,\"cursor_to\":3}"));

		assertThrows(IllegalStateException.class, call);
		assertThat(bundles.saved).isEmpty();
		assertThat(state.advanced).isEmpty();
	}

	@Test
	void 역전된_cursor_범위는_계약_위반으로_거부한다() {
		RecordingBundleRepo bundles = new RecordingBundleRepo();
		RecordingStateRepo state = new RecordingStateRepo();
		Executable call = () -> new BundleIngestor(bundles, state)
				.ingest(bundle("{\"cursor_from\":5,\"cursor_to\":3}"));

		assertThrows(IllegalStateException.class, call);
		assertThat(bundles.saved).isEmpty();
	}
}
