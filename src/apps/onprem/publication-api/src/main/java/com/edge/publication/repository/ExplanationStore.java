package com.edge.publication.repository;

import com.edge.publication.entity.AnalysisItem;
import com.edge.publication.entity.Publication;
import com.github.benmanes.caffeine.cache.Cache;
import com.github.benmanes.caffeine.cache.Caffeine;
import com.github.benmanes.caffeine.cache.Ticker;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.Limit;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.Duration;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.Set;

/**
 * Published 설명 조회 — Publication API 의 유일한 데이터 소스.
 * 온프렘 Published Store(publication ⋈ analysis_item)를 {@link PublicationRepository}로 조회하며,
 * WHERE 절이 Published(그리고 노출 가능 상태 AUTO_PUBLISHED·APPROVED)만 허용하므로
 * 그 외 상태는 이 층을 통과할 수 없다(제품 보장 — 계약 publication-api.md).
 * 리포지토리 엔티티를 서비스가 쓰는 {@link PublishedExplanation} record 로 매핑한다.
 */
@Component
public class ExplanationStore {

	/** 조회 도메인 모델 — publication_id 는 온프렘 발번(identity). */
	public record PublishedExplanation(
			long publicationId,
			String ticker,
			String etfName,
			LocalDate tradeDate,
			String summary,
			String confidenceLevel,
			List<Evidence> evidences,
			OffsetDateTime publishedAt,
			/** 스냅샷 기준시각 — 표시 규칙 "유효 최신 승리"의 축, 응답 노출(ALPHA-743). */
			OffsetDateTime explanationAsOf,
			/** 콘텐츠 기준시각(ALPHA-918) — 산문이 서술하는 창의 끝. 구형 게시분은 null. */
			OffsetDateTime contentAsOf
	) {
		public record Evidence(String kind, String title, String source, OffsetDateTime publishedAt) {
		}
	}

	private final PublicationRepository publications;
	private final Set<String> knownTickers;
	private final ObjectMapper objectMapper = new ObjectMapper();
	private final Cache<String, Optional<PublishedExplanation>> serveCache;

	@Autowired
	public ExplanationStore(PublicationRepository publications,
			@Value("${publication.known-tickers}") Set<String> knownTickers,
			@Value("${publication.serve-cache-ttl:3s}") Duration serveCacheTtl) {
		this(publications, knownTickers, serveCacheTtl, Ticker.systemTicker());
	}

	// 테스트 시간 주입 시임 — TTL 만료(스테일 상한)를 실제 대기 없이 검증한다.
	ExplanationStore(PublicationRepository publications, Set<String> knownTickers,
			Duration serveCacheTtl, Ticker ticker) {
		this.publications = publications;
		this.knownTickers = knownTickers;
		// 조회 캐시(ALPHA-433) — 급등 시 동일 종목 집중 조회(hot-key)의 중복 읽기를 제거한다.
		// 응답은 고객별 요소가 없어 (ticker, trade_date) 단위로 공유 가능하다(고객 컨텍스트
		// 폐지 — ADR-0053. 요청 메트릭 기록은 캐시 밖 필터라 캐시 적중과 무관하게 남는다).
		// 검수·차단 이벤트의 프로세스 간 무효화 경로가 없으므로 TTL 이 곧 차단·정정 반영
		// 지연의 상한이다 — 늘릴 때는 컴플라이언스 검토가 선행돼야 한다.
		// "게시분 없음"(empty)도 캐시한다: 신규 게시 노출이 최대 TTL 만큼 늦는 대신
		// 204 폭주도 같은 상한으로 막는다.
		this.serveCache = Caffeine.newBuilder()
				.expireAfterWrite(serveCacheTtl)
				.maximumSize(10_000)
				.ticker(ticker)
				.build();
	}

	/** 상장 여부(404 판별) — 종목 마스터 동기화 전의 설정 allowlist. */
	public boolean isKnownTicker(String ticker) {
		return knownTickers.contains(ticker);
	}

	/**
	 * 해당 ETF·일자의 Published 설명. trade_date 가 null 이면 **최신 거래일**의 게시분 —
	 * 화면(MTS AI 탭)은 "가장 최근 거래일의 분석"을 원하므로 게시 시각이 아니라
	 * 거래일을 우선 정렬한다(과거일 검수분이 늦게 게시돼도 최신 거래일이 이긴다).
	 */
	public Optional<PublishedExplanation> findPublished(String ticker, LocalDate tradeDate) {
		// 같은 키의 동시 미스는 Caffeine 이 로더 1회로 합친다(stampede 방지).
		return serveCache.get(cacheKey(ticker, tradeDate), key -> load(ticker, tradeDate));
	}

	private static String cacheKey(String ticker, LocalDate tradeDate) {
		return tradeDate == null ? ticker + "|latest" : ticker + "|" + tradeDate;
	}

	// package-private: 캐시 테스트(ExplanationStoreCacheTest)가 로더를 대역으로 바꿔
	// positive/negative 캐싱을 엔티티 조립 없이 검증한다(엔티티는 JPA 전용 protected 생성자뿐).
	Optional<PublishedExplanation> load(String ticker, LocalDate tradeDate) {
		Optional<Publication> found = tradeDate == null
				? publications.findLatestPublished(ticker, Limit.of(1))
				: publications.findPublishedOn(ticker, tradeDate, Limit.of(1));
		return found.map(this::toDomain);
	}

	private PublishedExplanation toDomain(Publication p) {
		AnalysisItem a = p.getAnalysisItem();
		// 노출 문구는 게시 시점 스냅샷(published_summary)이 우선이다 — 수정 승인(ALPHA-437)의
		// 편집 문구가 여기로 노출된다. NULL(자동 게시·기존 행)은 analysis_item 원문 폴백.
		String summary = p.getPublishedSummary() != null ? p.getPublishedSummary() : a.getSummary();
		return new PublishedExplanation(
				p.getPublicationId(),
				p.getEtfTicker(),
				a.getEtfName(),
				p.getTradeDate(),
				summary,
				a.getConfidenceLevel(),
				parseEvidences(a.getEvidences()),
				p.getPublishedAt(),
				p.getExplanationAsOf(),
				p.getContentAsOf());
	}

	/**
	 * analysis_item.evidences JSONB — [{kind, title, source, published_at, source_uri}] (번들 경계면
	 * 형상 — source_uri 는 검수 콘솔용(ALPHA-739)이라 서빙에선 읽지 않는다).
	 * 형상 위반(배열 아님·비객체 요소)은 조용히 빈 근거로 만들지 않고 즉시 실패시킨다
	 * (Rule 12 fail-loud — 저장 데이터 오류를 200 응답이 은폐하면 안 된다).
	 */
	// package-private: 계약 테스트(EventBundleContractTest)가 실제 파싱을 직접 검증한다 —
	// 스키마 evidences 형상과 이 파싱 키(kind/title/source/published_at)의 드리프트를 잡는다.
	List<PublishedExplanation.Evidence> parseEvidences(String json) {
		if (json == null || json.isBlank()) {
			return List.of();
		}
		JsonNode root = objectMapper.readTree(json);
		if (root.isNull()) {
			return List.of();
		}
		if (!root.isArray()) {
			throw new IllegalStateException("evidences JSONB 가 배열이 아니다: " + root.getNodeType());
		}
		List<PublishedExplanation.Evidence> evidences = new ArrayList<>();
		for (JsonNode node : root) {
			if (!node.isObject()) {
				throw new IllegalStateException("evidences 요소가 객체가 아니다: " + node.getNodeType());
			}
			evidences.add(new PublishedExplanation.Evidence(
					node.path("kind").asString(null),
					node.path("title").asString(null),
					node.path("source").asString(null),
					node.hasNonNull("published_at")
							? OffsetDateTime.parse(node.get("published_at").asString())
							: null));
		}
		return evidences;
	}
}
