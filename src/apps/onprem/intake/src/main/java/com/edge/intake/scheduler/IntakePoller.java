package com.edge.intake.scheduler;

import com.edge.intake.client.SyncAgentClient;
import com.edge.intake.client.SyncAgentClient.PulledBundle;
import com.edge.intake.repository.SyncStateRepository;
import com.edge.intake.service.BundleIngestor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.OptionalLong;

/**
 * 폴링 루프 — committed cursor(sync_state)에서 재개해 신규 번들이 없을 때까지 드레인.
 * 실패는 틱을 중단하고 로그로 표면화한다(fail-loud) — 다음 틱이 같은 cursor 에서
 * 재시도하므로 유실이 없다(at-least-once).
 */
@Component
public class IntakePoller {

	private static final Logger log = LoggerFactory.getLogger(IntakePoller.class);

	private final SyncAgentClient syncAgentClient;
	private final BundleIngestor bundleIngestor;
	private final SyncStateRepository syncStateRepository;
	private final int maxBundlesPerTick;

	public IntakePoller(SyncAgentClient syncAgentClient, BundleIngestor bundleIngestor,
			SyncStateRepository syncStateRepository,
			@Value("${intake.max-bundles-per-tick}") int maxBundlesPerTick) {
		this.syncAgentClient = syncAgentClient;
		this.bundleIngestor = bundleIngestor;
		this.syncStateRepository = syncStateRepository;
		this.maxBundlesPerTick = maxBundlesPerTick;
	}

	@Scheduled(fixedDelayString = "${intake.poll-delay-ms}")
	public void poll() {
		try {
			long cursor = syncStateRepository.lastCursor();
			for (int i = 0; i < maxBundlesPerTick; i++) {
				PulledBundle bundle = syncAgentClient.pull(cursor);
				// 신규 없음(result 생략 성공 포맷, ADR-0042)의 유일한 신호 — 이번 틱 종료.
				OptionalLong advanced = bundleIngestor.ingest(bundle);
				if (advanced.isEmpty()) {
					return;
				}
				cursor = advanced.getAsLong();
			}
			log.info("tick 상한 도달(maxBundlesPerTick={}) — 잔여분은 다음 틱에", maxBundlesPerTick);
		} catch (Exception e) {
			// 틱 실패 = 이번 라운드 중단. committed cursor 는 트랜잭션 밖에서 전진하지 않으므로
			// 다음 틱이 같은 지점에서 재-Pull 한다(중복은 cursor dedup 으로 무해).
			log.error("intake 폴링 실패 — 다음 틱에 재시도", e);
		}
	}
}
