package com.edge.superadmin.mock;

import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * analyses 도메인 mock 데이터 — super-admin-ui 시안(v0.1) 목데이터 이식(ALPHA-515).
 * in-memory 가변 스토어(앱 전역 singleton — 재기동 시 초기화). DB 연동 시
 * service 의 이 스토어 호출부를 분석 이벤트 테이블 조회/전이로 교체한다.
 */
@Component
public class AnalysisMockStore {

	public record Evidence(String type, String title, String source, String time) {
	}

	public record Analysis(String id, String name, String code, String market, int direction,
			double changePct, String status, String basisTime, String basisTimeAbs,
			String doneTime, int score, boolean corrected, String result,
			List<Evidence> evidence) {

		Analysis withResult(String newResult) {
			return new Analysis(id, name, code, market, direction, changePct, status, basisTime,
					basisTimeAbs, doneTime, score, true, newResult, evidence);
		}

		Analysis withStatus(String newStatus) {
			return new Analysis(id, name, code, market, direction, changePct, newStatus, basisTime,
					basisTimeAbs, doneTime, score, corrected, result, evidence);
		}
	}

	private final List<Analysis> analyses = new ArrayList<>(List.of(
			new Analysis("a1", "삼성전자", "005930", "KRX", 1, 3.42, "COMPLETED",
					"07-10 09:12", "2026-07-10 09:12 KST", "2026-07-10 09:14 KST", 78, false,
					"3분기 잠정 실적이 시장 컨센서스(영업이익 11.2조)를 14% 상회하며 반도체 부문 회복이 확인됨. "
							+ "HBM4 양산 일정 조기화 언급이 외국인 순매수를 견인, 개장 직후 대량 매수세로 이어진 것으로 분석됨.\n"
							+ "반도체 비중이 높은 ETF(KODEX 반도체, TIGER Fn반도체TOP10)에 직접적 상방 영향.",
					List.of(
							new Evidence("공시", "삼성전자, 2026년 3분기 잠정 실적 공시 (연결 영업이익 12.8조)",
									"KIND", "2026-07-10 08:32"),
							new Evidence("뉴스", "\"HBM4 양산 앞당긴다\"… 삼성전자, 하반기 공급 계약 확대",
									"연합인포맥스", "2026-07-10 08:51"),
							new Evidence("시세", "개장 후 10분간 거래대금 4,120억 · 외국인 순매수 1,840억",
									"KRX 시세", "2026-07-10 09:12"))),
			new Analysis("a2", "SK하이닉스", "000660", "KRX", 1, 5.18, "COMPLETED",
					"07-10 09:31", "2026-07-10 09:31 KST", "2026-07-10 09:33 KST", 84, false,
					"삼성전자 호실적 공시에 따른 반도체 섹터 동반 강세에 더해, 엔비디아향 HBM 추가 수주 보도가 "
							+ "상승 폭을 확대함. 거래량이 20일 평균 대비 3.1배로 수급 쏠림이 뚜렷함.",
					List.of(
							new Evidence("뉴스", "SK하이닉스, 엔비디아 차세대 GPU향 HBM4 추가 수주 임박",
									"뉴스핌", "2026-07-10 09:05"),
							new Evidence("시세", "거래량 20일 평균 대비 312% · 기관 순매수 920억",
									"KRX 시세", "2026-07-10 09:31"))),
			new Analysis("a3", "에코프로비엠", "247540", "KRX", -1, 6.71, "COMPLETED",
					"07-10 10:05", "2026-07-10 10:05 KST", "2026-07-10 10:08 KST", 71, false,
					"주요 고객사의 북미 전기차 판매 부진 발표로 양극재 수주 축소 우려가 확산됨. 2차전지 섹터 "
							+ "전반의 동반 약세 속 낙폭 최대. 2차전지 테마 ETF의 하방 압력 요인.",
					List.of(
							new Evidence("뉴스", "북미 전기차 2분기 판매 전년比 18% 감소… 배터리 소재주 급락",
									"연합인포맥스", "2026-07-10 09:48"),
							new Evidence("실적 / 재무", "에코프로비엠 2분기 영업이익 컨센서스 하향 (−22%)",
									"DART 재무", "2026-07-10 09:55"))),
			new Analysis("a4", "셀트리온", "068270", "KRX", -1, 3.05, "PENDING",
					"07-10 13:44", "2026-07-10 13:44 KST", "—", 0, false,
					"분석 대기 중입니다. 근거 데이터 수집이 완료되면 자동으로 분석이 시작됩니다.",
					List.of(
							new Evidence("시세", "오후 급락 감지 · 거래량 20일 평균 대비 189%",
									"KRX 시세", "2026-07-10 13:44"))),
			new Analysis("a5", "POSCO홀딩스", "005490", "KRX", 1, 4.12, "COMPLETED",
					"07-10 09:58", "2026-07-10 09:58 KST", "2026-07-10 10:01 KST", 65, false,
					"리튬 국제 가격 반등과 아르헨티나 염호 2단계 상업 생산 개시 공시가 겹치며 이차전지 소재 "
							+ "밸류체인 기대감이 재부각됨.",
					List.of(
							new Evidence("공시", "POSCO홀딩스, 아르헨티나 리튬 염호 2단계 상업 생산 개시",
									"KIND", "2026-07-10 09:40"),
							new Evidence("뉴스", "탄산리튬 가격 3주 연속 상승… 소재주 반등 조짐",
									"뉴스핌", "2026-07-10 09:21"))),
			new Analysis("a6", "카카오", "035720", "KRX", -1, 4.88, "FAILED",
					"07-10 11:20", "2026-07-10 11:20 KST", "—", 0, false,
					"분석 실패: 근거 데이터 수집 단계에서 뉴스 소스 응답 시간 초과(timeout)가 발생했습니다. "
							+ "재시도 큐에 등록되어 있습니다.",
					List.of(
							new Evidence("시세", "오전 급락 감지 · 공매도 잔고 비율 상승",
									"KRX 시세", "2026-07-10 11:20"))),
			new Analysis("a7", "두산에너빌리티", "034020", "KRX", 1, 7.35, "COMPLETED",
					"07-10 09:22", "2026-07-10 09:22 KST", "2026-07-10 09:25 KST", 88, false,
					"체코 신규 원전 2기 추가 수주 우선협상대상자 선정 보도로 개장 직후 급등. 원전 테마 전반으로 "
							+ "매수세가 확산되며 관련 ETF 상방 영향이 큼.",
					List.of(
							new Evidence("뉴스", "두산에너빌리티, 체코 신규 원전 2기 우선협상대상자 선정",
									"연합인포맥스", "2026-07-10 08:47"),
							new Evidence("시세", "개장 동시호가 매수 잔량 급증 · 상한가 근접 후 되돌림",
									"KRX 시세", "2026-07-10 09:22"))),
			new Analysis("a8", "LG에너지솔루션", "373220", "KRX", -1, 3.6, "PENDING",
					"07-10 14:10", "2026-07-10 14:10 KST", "—", 0, false,
					"분석 대기 중입니다. 근거 데이터 수집이 완료되면 자동으로 분석이 시작됩니다.",
					List.of(
							new Evidence("시세", "오후 하락 감지 · 외국인 순매도 전환",
									"KRX 시세", "2026-07-10 14:10"))),
			new Analysis("a9", "NVIDIA", "NVDA", "NASDAQ", 1, 4.92, "COMPLETED",
					"07-11 05:12", "2026-07-10 16:12 EDT (07-11 05:12 KST)", "2026-07-11 05:15 KST",
					82, false,
					"차세대 데이터센터 GPU 출하 가이던스 상향 보도와 대형 클라우드 3사의 CapEx 증액 발표가 "
							+ "겹치며 정규장 마감까지 강세 지속. 국내 상장 미국 반도체 ETF 및 TIGER 미국나스닥100에 상방 영향.",
					List.of(
							new Evidence("뉴스", "NVIDIA, next-gen GPU shipment guidance raised for H2",
									"Reuters", "2026-07-10 14:02 EDT"),
							new Evidence("뉴스", "대형 클라우드 3사, AI 인프라 CapEx 일제 증액",
									"연합인포맥스", "2026-07-11 04:40 KST"),
							new Evidence("시세", "정규장 거래대금 $48.2B · 종가 기준 사상 최고가 경신",
									"NASDAQ TotalView", "2026-07-10 16:00 EDT"))),
			new Analysis("a10", "Tesla", "TSLA", "NASDAQ", -1, 5.44, "COMPLETED",
					"07-11 04:48", "2026-07-10 15:48 EDT (07-11 04:48 KST)", "2026-07-11 04:52 KST",
					74, false,
					"2분기 인도량이 컨센서스를 9% 하회한 데 이어 주요 시장 가격 인하 발표로 마진 축소 우려가 "
							+ "부각됨. 장 마감 직전 낙폭 확대.",
					List.of(
							new Evidence("실적 / 재무", "Tesla Q2 deliveries miss consensus by 9%",
									"SEC/IR", "2026-07-10 09:30 EDT"),
							new Evidence("뉴스", "Tesla, 미국·유럽 주요 모델 가격 인하 발표",
									"Reuters", "2026-07-10 15:20 EDT"))),
			new Analysis("a11", "Apple", "AAPL", "NASDAQ", 1, 2.87, "PENDING",
					"07-11 05:00", "2026-07-10 16:00 EDT (07-11 05:00 KST)", "—", 0, false,
					"분석 대기 중입니다. 근거 데이터 수집이 완료되면 자동으로 분석이 시작됩니다.",
					List.of(
							new Evidence("시세", "종가 기준 상승 감지 · 거래량 평균 대비 141%",
									"NASDAQ TotalView", "2026-07-10 16:00 EDT"))),
			new Analysis("a12", "Super Micro Computer", "SMCI", "NASDAQ", -1, 8.12, "EXCLUDED",
					"07-11 04:30", "2026-07-10 15:30 EDT (07-11 04:30 KST)", "2026-07-11 04:35 KST",
					55, false,
					"회계 이슈 관련 미확인 보도로 급락. 근거 데이터의 신뢰도가 기준 미달로 판단되어 운영자가 "
							+ "분석 대상에서 제외함.",
					List.of(
							new Evidence("뉴스", "회계 감사인 교체설 보도 (미확인)",
									"SNS/커뮤니티", "2026-07-10 14:55 EDT")))));

	/* 제외 전 상태 기억 — 복원이 미완료(FAILED·PENDING) 건을 COMPLETED 로 둔갑시키지 않게 */
	private final Map<String, String> statusBeforeExclusion = new HashMap<>();

	public synchronized List<Analysis> list() {
		return List.copyOf(analyses);
	}

	public synchronized boolean correct(String id, String result) {
		return patch(id, a -> a.withResult(result));
	}

	public synchronized boolean exclude(String id) {
		return patch(id, a -> {
			statusBeforeExclusion.put(id, a.status());
			return a.withStatus("EXCLUDED");
		});
	}

	public synchronized boolean restore(String id) {
		String prev = statusBeforeExclusion.getOrDefault(id, "COMPLETED");
		boolean patched = patch(id, a -> a.withStatus(prev));
		if (patched) {
			statusBeforeExclusion.remove(id);
		}
		return patched;
	}

	/** 없는 ID 는 false — real 구현의 404 와 같은 의미로 실패를 드러낸다. */
	private boolean patch(String id, java.util.function.UnaryOperator<Analysis> updater) {
		Optional<Analysis> found = analyses.stream().filter(a -> a.id().equals(id)).findFirst();
		if (found.isEmpty()) {
			return false;
		}
		analyses.replaceAll(a -> a.id().equals(id) ? updater.apply(a) : a);
		return true;
	}
}
