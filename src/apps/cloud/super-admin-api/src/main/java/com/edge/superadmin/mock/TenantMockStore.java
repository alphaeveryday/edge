package com.edge.superadmin.mock;

import org.springframework.stereotype.Component;

import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;

/**
 * tenants 도메인 mock 데이터 — super-admin-ui 시안(v0.1) 목데이터 이식(ALPHA-515).
 * in-memory 가변 스토어(앱 전역 singleton — 재기동 시 초기화, 사용자별 격리 없음:
 * mock 단계의 의도적 트레이드오프). DB 연동 시 service 의 이 스토어 호출부를
 * tenant 테이블 조회로 교체한다.
 */
@Component
public class TenantMockStore {

	public record Tenant(String id, String name, String domain, String env, String status,
			String admin, String email, String created, String lastSync, String lastSyncAbs,
			int calls, int errors, List<Integer> bars) {
	}

	/* 시안의 시드 PRNG 이식 — 시간대별 호출량 24칸 (07~10시 구간 피크, 지연 시 최근 급감) */
	private static List<Integer> bars(int seed, boolean lag) {
		long x = seed;
		List<Integer> arr = new ArrayList<>(24);
		for (int i = 0; i < 24; i++) {
			x = (x * 9301 + 49297) % 233280;
			int v = 18 + (int) Math.round((x / 233280.0) * 78);
			if (i >= 7 && i <= 10) {
				v = Math.min(96, v + 24);
			}
			if (lag && i > 19) {
				v = 4;
			}
			arr.add(v);
		}
		// 불변으로 봉인 — list() 가 record 를 그대로 내보내므로 내부 리스트가 밖에서
		// 변조되면 singleton 스토어가 오염된다(구 UI mock 의 복사 방어와 동등).
		return List.copyOf(arr);
	}

	private static List<Integer> scaled(List<Integer> source, double factor) {
		return source.stream().map(v -> (int) Math.round(v * factor)).toList();
	}

	private final List<Tenant> tenants = new ArrayList<>(List.of(
			new Tenant("t1", "미래에셋증권", "miraeasset.com", "Prod", "ACTIVE", "김도현",
					"dohyun.kim@miraeasset.com", "2025-11-03", "2분 전", "2026-07-12 10:22 KST",
					48214, 12, bars(11, false)),
			new Tenant("t2", "한국투자증권", "koreainvestment.com", "Prod", "ACTIVE", "박서연",
					"sy.park@koreainvestment.com", "2025-12-18", "1분 전", "2026-07-12 10:23 KST",
					39082, 4, bars(23, false)),
			new Tenant("t3", "NH투자증권", "nhqv.com", "Prod", "SYNC_DELAYED", "이준호",
					"jh.lee@nhqv.com", "2026-01-22", "47분 전", "2026-07-12 09:37 KST",
					21540, 138, bars(37, true)),
			new Tenant("t4", "삼성증권", "samsungpop.com", "Prod", "ACTIVE", "최유진",
					"yj.choi@samsungpop.com", "2026-02-09", "3분 전", "2026-07-12 10:21 KST",
					35617, 9, bars(51, false)),
			new Tenant("t5", "KB증권", "kbsec.com", "Dev", "ONBOARDING", "정민재",
					"mj.jung@kbsec.com", "2026-06-30", "—", "동기화 이력 없음",
					1204, 0, scaled(bars(67, false), 0.15)),
			new Tenant("t6", "키움증권", "kiwoom.com", "Prod", "ACTIVE", "한지우",
					"jw.han@kiwoom.com", "2026-03-14", "2분 전", "2026-07-12 10:22 KST",
					52931, 21, bars(83, false)),
			new Tenant("t7", "신한투자증권", "shinhansec.com", "Dev", "SYNC_DELAYED", "오세훈",
					"sh.oh@shinhansec.com", "2026-04-02", "1시간 12분 전", "2026-07-12 09:12 KST",
					8462, 64, bars(97, true)),
			new Tenant("t8", "하나증권", "hanaw.com", "Dev", "ONBOARDING", "임수아",
					"sa.lim@hanaw.com", "2026-07-08", "—", "동기화 이력 없음",
					316, 2, scaled(bars(113, false), 0.08))));

	private int nextId = 9;

	public synchronized List<Tenant> list() {
		return List.copyOf(tenants);
	}

	/** 신규 테넌트는 목록 맨 앞, 연결 상태는 온보딩(동기화 이력 없음)으로 시작한다. */
	public synchronized void create(String name, String env, String admin, String email) {
		int at = email.indexOf('@');
		String domain = at >= 0 ? email.substring(at + 1) : "";
		tenants.add(0, new Tenant("t" + nextId++, name, domain, env, "ONBOARDING", admin, email,
				LocalDate.now().toString(), "—", "동기화 이력 없음", 0, 0,
				List.copyOf(java.util.Collections.nCopies(24, 2))));
	}
}
