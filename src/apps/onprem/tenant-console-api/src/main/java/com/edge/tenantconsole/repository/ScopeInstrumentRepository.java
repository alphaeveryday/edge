package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.entity.AnalysisItemEntity;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;

import java.util.List;

/**
 * 제공 범위 종목 유니버스 — analysis_item(실제 수신된 ETF)에서 조회한다. 온프렘 원장엔
 * 인스트루먼트 마스터가 없어(ETF 마스터는 보류, serving_scope 마이그레이션 주석) 수신
 * 이력이 곧 이 박스가 아는 종목 집합이다. 검수 전이 repository(ReviewItemRepository)와
 * 같은 analysis_item 을 읽지만 관심사(제공 범위 유니버스 vs 검수 큐)가 달라 분리한다.
 */
public interface ScopeInstrumentRepository extends Repository<AnalysisItemEntity, String> {

	/** 유니버스 조회 결과 투영 — 종목 코드(서빙 키)와 이름만. */
	interface ScopeInstrumentRow {
		String getCode();

		String getName();
	}

	/**
	 * 티커별 1건(최신 수신 이름)으로 유니버스를 조립한다 — 같은 티커가 여러 항목으로
	 * 수신되며 이름이 갱신될 수 있어 DISTINCT ON 으로 최신 이름을 고른다. 정렬 동률
	 * (한 번들의 여러 행은 received_at=now() 가 같다)은 explanation_as_of·PK(결정적)로
	 * 깨 임의 행이 뽑히지 않게 한다. code·name 둘 다 있어야 한다 — 번들 파서가 빈
	 * 문자열을 거르지 않고(strictString)·테이블에도 nonblank 제약이 없어, 빈 ticker 는
	 * 토글 경로로 주소화 불가한 목록 항목을, 빈 name 은 UI 계약(비-null string) 위반을
	 * 낳으므로 결측·공백은 유니버스에서 제외한다.
	 */
	@Query(value = """
			SELECT DISTINCT ON (etf_ticker) etf_ticker AS code, etf_name AS name
			  FROM analysis_item
			 WHERE etf_ticker IS NOT NULL AND btrim(etf_ticker, E' \\t\\n\\r') <> ''
			   AND etf_name IS NOT NULL AND btrim(etf_name, E' \\t\\n\\r') <> ''
			 ORDER BY etf_ticker, received_at DESC, explanation_as_of DESC, explanation_result_id DESC
			""", nativeQuery = true)
	List<ScopeInstrumentRow> findUniverse();

	/** 토글 대상 존재 검증(404 판정) — 유니버스 술어(코드·이름 결측·공백 아님)와 동일해야 한다. */
	@Query(value = """
			SELECT EXISTS(SELECT 1 FROM analysis_item
			               WHERE etf_ticker = :ticker AND btrim(etf_ticker, E' \\t\\n\\r') <> ''
			                 AND etf_name IS NOT NULL AND btrim(etf_name, E' \\t\\n\\r') <> '')
			""", nativeQuery = true)
	boolean existsInUniverse(@Param("ticker") String ticker);
}
