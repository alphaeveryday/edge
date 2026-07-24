package com.edge.tenantconsole.mock;

import org.springframework.stereotype.Component;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * explanations 도메인 mock 데이터 — UI 시안(v0.2) 목데이터를 서버로 이식한 in-memory
 * 가변 스토어. 상태 전이가 반영돼 UI 의 mutation → refetch 흐름이 실연동과 동일하게
 * 동작한다. DB 연동 시 이 스토어 호출부(service)를 repository 로 교체한다(ALPHA-513).
 * 상태 코드는 도메인 상태기계(state-machine.md) 어휘를 따른다.
 */
@Component
public class ExplanationMockStore {

	public record Evidence(String type, String title, String source, String time) {
	}

	public record Explanation(
			long id,
			String name,
			String code,
			String market,
			int direction,
			double changePct,
			String status,
			String risk,
			String reviewReason,
			String receivedRelative,
			String receivedAt,
			List<Evidence> evidence,
			String original,
			String finalText
	) {
	}

	public record FeedStatus(String state, String lastReceivedRelative, int todayReceived) {
	}

	private final Map<Long, Explanation> items = new LinkedHashMap<>();

	public ExplanationMockStore() {
		for (Explanation it : seed()) {
			items.put(it.id(), it);
		}
	}

	public synchronized List<Explanation> findAll() {
		return List.copyOf(items.values());
	}

	public FeedStatus feedStatus() {
		return new FeedStatus("NORMAL", "2분 전", 128);
	}

	public synchronized boolean updateFinal(long id, String finalText) {
		return patch(id, it -> with(it, it.status(), it.reviewReason(), finalText));
	}

	public synchronized boolean stop(long id) {
		return patch(id, it -> with(it, "UNPUBLISHED", it.reviewReason(), it.finalText()));
	}

	public synchronized boolean moveToReview(long id) {
		return patch(id, it -> with(it, "REVIEW_REQUIRED", it.reviewReason(), it.finalText()));
	}

	public synchronized boolean approve(long id, String finalText) {
		// 검수자 승인은 자동 제공과 구분되는 APPROVED — 승인과 함께 검수 사유가 해소된다.
		return patch(id, it -> with(it, "APPROVED", null, finalText));
	}

	public synchronized boolean reject(long id) {
		return patch(id, it -> with(it, "REJECTED", it.reviewReason(), it.finalText()));
	}

	public synchronized boolean saveDraft(long id, String finalText) {
		return patch(id, it -> with(it, it.status(), it.reviewReason(), finalText));
	}

	private boolean patch(long id, java.util.function.UnaryOperator<Explanation> updater) {
		Explanation it = items.get(id);
		if (it == null) {
			return false;
		}
		items.put(id, updater.apply(it));
		return true;
	}

	// mutation 이 바꾸는 축은 status·reviewReason·finalText 뿐이다.
	private static Explanation with(Explanation it, String status, String reviewReason, String finalText) {
		return new Explanation(it.id(), it.name(), it.code(), it.market(), it.direction(),
				it.changePct(), status, it.risk(), reviewReason, it.receivedRelative(),
				it.receivedAt(), it.evidence(), it.original(), finalText);
	}

	// 시안(v0.2) 목데이터 — tenant-console-ui 의 구 repository.mock.ts 와 동일(순서 포함).
	private static List<Explanation> seed() {
		return List.of(
				new Explanation(1, "삼성전자", "005930", "KRX", 1, 3.24,
						"AUTO_PUBLISHED", "LOW", null, "9분 전", "2026-07-11 10:42 KST",
						List.of(
								new Evidence("공시", "3분기 잠정 실적 발표 — 영업이익 컨센서스 12% 상회", "KIND", "10:31"),
								new Evidence("뉴스", "삼성전자, 반도체 부문 회복세 뚜렷", "연합인포맥스", "10:38")),
						"3분기 잠정 영업이익이 시장 예상치를 12% 상회하는 공시가 발표된 이후 기관 중심의 매수세가 유입되며 상승했습니다.",
						"3분기 잠정 영업이익이 시장 예상치를 12% 상회하는 공시가 발표된 이후 기관 중심의 매수세가 유입되며 상승했습니다."),
				new Explanation(2, "에코프로비엠", "247540", "KRX", 1, 8.91,
						"REVIEW_REQUIRED", "HIGH", "ASSERTIVE", "14분 전", "2026-07-11 10:37 KST",
						List.of(
								new Evidence("공시", "북미 완성차 업체와 2차전지 양극재 장기 공급계약 체결", "KIND", "10:22"),
								new Evidence("뉴스", "에코프로비엠, 대규모 수주 소식에 급등", "이데일리", "10:29")),
						"북미 완성차 업체와의 대규모 양극재 공급계약 공시로 급등했으며, 추가 상승이 확실시됩니다.",
						"북미 완성차 업체와의 대규모 양극재 공급계약 공시로 급등했으며, 추가 상승이 확실시됩니다."),
				new Explanation(3, "SK하이닉스", "000660", "KRX", -1, 2.15,
						"AUTO_PUBLISHED", "LOW", null, "22분 전", "2026-07-11 10:29 KST",
						List.of(
								new Evidence("뉴스", "필라델피아 반도체지수 1.8% 하락 마감", "연합인포맥스", "08:10"),
								new Evidence("뉴스", "외국인, 반도체 대형주 차익 실현 매도", "머니투데이", "10:05")),
						"간밤 필라델피아 반도체지수 하락과 외국인 차익 실현 매물이 겹치며 하락했습니다.",
						"간밤 필라델피아 반도체지수 하락과 외국인 차익 실현 매물이 겹치며 하락했습니다."),
				new Explanation(4, "NVIDIA", "NVDA", "NASDAQ", 1, 4.87,
						"AUTO_PUBLISHED", "MEDIUM", null, "38분 전", "2026-07-11 10:13 KST",
						List.of(
								new Evidence("공시", "차세대 AI 가속기 출하 일정 발표 (8-K)", "SEC EDGAR", "05:12"),
								new Evidence("뉴스", "NVIDIA, 신제품 수요 전망 상향에 강세", "Reuters", "05:40")),
						"차세대 AI 가속기 출하 일정 공시와 수요 전망 상향 보도가 이어지며 상승했습니다.",
						"차세대 AI 가속기 출하 일정 공시와 수요 전망 상향 보도가 이어지며 상승했습니다."),
				new Explanation(5, "카카오", "035720", "KRX", -1, 5.62,
						"REVIEW_REQUIRED", "MEDIUM", "SINGLE_SOURCE", "41분 전", "2026-07-11 10:10 KST",
						List.of(
								new Evidence("뉴스", "카카오, 주요 계열사 매각 검토설에 약세", "단독 보도 (1개 매체)", "09:52")),
						"주요 계열사 매각 검토 보도가 나오면서 투자 심리가 위축되어 하락했습니다.",
						"주요 계열사 매각 검토 보도가 나오면서 투자 심리가 위축되어 하락했습니다."),
				new Explanation(6, "Tesla", "TSLA", "NASDAQ", -1, 6.33,
						"BLOCKED", "HIGH", "BANNED_WORD", "1시간 전", "2026-07-11 09:48 KST",
						List.of(
								new Evidence("뉴스", "2분기 인도량 시장 예상치 하회", "Bloomberg", "05:02")),
						"2분기 인도량 부진으로 급락했으며, 반등 전까지 전량 매도가 유리합니다.",
						"2분기 인도량 부진으로 급락했으며, 반등 전까지 전량 매도가 유리합니다."),
				new Explanation(7, "셀트리온", "068270", "KRX", 1, 2.05,
						"AUTO_PUBLISHED", "LOW", null, "1시간 전", "2026-07-11 09:41 KST",
						List.of(
								new Evidence("공시", "바이오시밀러 신제품 유럽 판매 승인", "KIND", "09:02"),
								new Evidence("뉴스", "셀트리온, 유럽 승인 소식에 상승", "한국경제", "09:15")),
						"바이오시밀러 신제품의 유럽 판매 승인 공시 이후 매수세가 유입되며 상승했습니다.",
						"바이오시밀러 신제품의 유럽 판매 승인 공시 이후 매수세가 유입되며 상승했습니다."),
				new Explanation(8, "Apple", "AAPL", "NASDAQ", 1, 1.12,
						"AUTO_PUBLISHED", "LOW", null, "2시간 전", "2026-07-11 08:55 KST",
						List.of(
								new Evidence("뉴스", "서비스 부문 매출 성장 지속 전망", "WSJ", "05:20"),
								new Evidence("뉴스", "애플, 기관 매수세에 소폭 상승", "Reuters", "05:45")),
						"서비스 부문 매출 성장 전망 보도와 기관 매수세에 힘입어 소폭 상승했습니다.",
						"서비스 부문 매출 성장 전망 보도와 기관 매수세에 힘입어 소폭 상승했습니다."),
				new Explanation(9, "포스코퓨처엠", "003670", "KRX", -1, 4.48,
						"REVIEW_REQUIRED", "HIGH", "BANNED_WORD", "2시간 전", "2026-07-11 08:47 KST",
						List.of(
								new Evidence("뉴스", "리튬 가격 하락에 2차전지 소재주 동반 약세", "연합인포맥스", "08:12"),
								new Evidence("뉴스", "포스코퓨처엠, 원재료 가격 부담 지속", "서울경제", "08:30")),
						"리튬 가격 하락으로 소재주 전반이 약세를 보였으며, 목표가 돌파 예정 시점은 지연될 전망입니다.",
						"리튬 가격 하락으로 소재주 전반이 약세를 보였으며, 목표가 돌파 예정 시점은 지연될 전망입니다."),
				new Explanation(10, "두산에너빌리티", "034020", "KRX", 1, 6.74,
						"AUTO_PUBLISHED", "MEDIUM", null, "3시간 전", "2026-07-11 07:58 KST",
						List.of(
								new Evidence("공시", "해외 원전 기자재 수주 계약 체결", "KIND", "07:31"),
								new Evidence("뉴스", "두산에너빌리티, 원전 수주 모멘텀 지속", "매일경제", "07:44")),
						"해외 원전 기자재 수주 공시가 발표되며 원전 관련 모멘텀이 부각되어 상승했습니다.",
						"해외 원전 기자재 수주 공시가 발표되며 원전 관련 모멘텀이 부각되어 상승했습니다."),
				new Explanation(11, "AMD", "AMD", "NASDAQ", -1, 3.29,
						"UNPUBLISHED", "MEDIUM", null, "4시간 전", "2026-07-11 06:50 KST",
						List.of(
								new Evidence("뉴스", "데이터센터 부문 경쟁 심화 우려", "Reuters", "05:31")),
						"데이터센터 부문 경쟁 심화 우려가 제기되며 하락했습니다.",
						"데이터센터 부문 경쟁 심화 우려가 제기되며 하락했습니다."),
				new Explanation(13, "Rivian", "RIVN", "NASDAQ", 1, 11.42,
						"REJECTED", "HIGH", "ASSERTIVE", "5시간 전", "2026-07-11 05:40 KST",
						List.of(
								new Evidence("뉴스", "신형 SUV 사전예약 개시, 초기 반응 호조", "TechCrunch", "04:55")),
						"신형 SUV 사전예약 개시 소식에 급등했으며, 강한 상승세가 계속될 것입니다.",
						"신형 SUV 사전예약 개시 소식에 급등했으며, 강한 상승세가 계속될 것입니다."),
				new Explanation(12, "LG에너지솔루션", "373220", "KRX", 1, 1.86,
						"AUTO_PUBLISHED", "LOW", null, "4시간 전", "2026-07-11 06:32 KST",
						List.of(
								new Evidence("공시", "유럽 배터리 합작법인 증설 투자 결정", "KIND", "06:02"),
								new Evidence("뉴스", "LG엔솔, 유럽 증설 투자에 강세", "한국경제", "06:15")),
						"유럽 배터리 합작법인 증설 투자 공시가 발표된 이후 매수세가 유입되며 상승했습니다.",
						"유럽 배터리 합작법인 증설 투자 공시가 발표된 이후 매수세가 유입되며 상승했습니다."));
	}
}
