package com.edge.screening.scheduler;

import com.edge.screening.repository.PendingBundleRepository;
import com.edge.screening.repository.PendingBundleRepository.PendingBundle;
import com.edge.screening.service.BundleScreener;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * 미점검 번들 폴링 — cursor 순 처리(sync-protocol.md: 무효화는 항상 원본보다 늦게
 * 도착하므로 순차 소비가 순서를 담보한다). 번들 실패는 뒤 번들 처리도 멈춘다 —
 * 순서를 건너뛰면 무효화가 원본보다 먼저 적용될 수 있다.
 */
@Component
public class ScreeningPoller {

	private static final Logger log = LoggerFactory.getLogger(ScreeningPoller.class);

	private final PendingBundleRepository pendingBundleRepository;
	private final BundleScreener bundleScreener;
	private final int maxBundlesPerTick;

	public ScreeningPoller(PendingBundleRepository pendingBundleRepository,
			BundleScreener bundleScreener,
			@Value("${screening.max-bundles-per-tick}") int maxBundlesPerTick) {
		this.pendingBundleRepository = pendingBundleRepository;
		this.bundleScreener = bundleScreener;
		this.maxBundlesPerTick = maxBundlesPerTick;
	}

	@Scheduled(fixedDelayString = "${screening.poll-delay-ms}")
	public void poll() {
		List<PendingBundle> pending;
		try {
			pending = pendingBundleRepository.findUnscreened(maxBundlesPerTick);
		} catch (Exception e) {
			log.error("미점검 번들 조회 실패 — 다음 틱에 재시도", e);
			return;
		}
		for (PendingBundle bundle : pending) {
			try {
				bundleScreener.screen(bundle.cursorFrom(), bundle.body());
			} catch (Exception e) {
				// 순서 보존: 실패 번들 뒤는 처리하지 않는다(무효화가 원본을 앞지르면 안 된다).
				log.error("번들 점검 실패 cursor_from={} — 순서 보존을 위해 이번 틱 중단, 다음 틱 재시도",
						bundle.cursorFrom(), e);
				return;
			}
		}
	}
}
