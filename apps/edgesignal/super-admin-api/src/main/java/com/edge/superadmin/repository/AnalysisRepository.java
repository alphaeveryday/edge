package com.edge.superadmin.repository;

import java.time.OffsetDateTime;
import java.util.List;

/**
 * analyses 화면이 읽는 설명 원장 조회(ALPHA-601). 트리거(가격 변동)부터 설명 결과까지를
 * 런 단위 한 행으로 낸다 — 축이 {@code explanation_result} 가 아니라 {@code explanation_run}
 * 인 이유는, 결과가 아직 없는 런(PENDING·RUNNING·FAILED)도 운영자가 봐야 하는 대상이기
 * 때문이다(결과 축이면 실패한 분석이 화면에서 통째로 사라진다).
 */
public interface AnalysisRepository {

	/** 최신 분석부터. 원장 어휘(run_status·confidence_level·MIC)를 그대로 낸다 — UI 어휘 번역은 표시 층 소관. */
	List<AnalysisRow> list();

	/**
	 * @param summary           결과가 아직 없는 런이면 null — 상태별 안내 문구는 표시 층이 정한다
	 * @param confidenceLevel   결과가 없거나 원장이 판정을 비웠으면 null
	 * @param publicationStatus 게시 수명주기(DRAFT/PUBLISHED/WITHDRAWN) — run_status 와 별개
	 *                          축이다(실행 상태 vs 게시 상태). 결과가 아직 없는 런이면 null.
	 *                          무효화 액션(ALPHA-440)의 활성 조건이 게시본(PUBLISHED)이라
	 *                          화면이 이 축을 알아야 한다(ALPHA-737)
	 * @param evidenceTotal     이 런의 문서 근거 총 건수. {@code evidence} 는 표시 상한까지만
	 *                          담기므로 둘이 다를 수 있다 — 화면이 "몇 건 중 몇 건"을 말하려면
	 *                          잘라낸 사실이 계약에 있어야 한다
	 * @param resultBlocks      고객 산문에 실제로 나간 블록들({@code stage_results ->
	 *                          final_explanation -> blocks}, ALPHA-878). 내부 산출(stat_tests
	 *                          버퍼·stage_results 원시값)은 이 계약에 싣지 않는다 — 콘솔이
	 *                          "고객에게 나간 문장"만 보이게 하는 경계가 여기다. 블록이 없는
	 *                          런(구 엔진·결과 없음)은 빈 목록
	 */
	record AnalysisRow(String runId, String etfName, String ticker, String marketCode,
			double observedReturn, String runStatus, OffsetDateTime detectedAt,
			OffsetDateTime finishedAt, String summary, String confidenceLevel,
			String publicationStatus, List<ResultBlock> resultBlocks,
			List<EvidenceRow> evidence, int evidenceTotal) {
	}

	/**
	 * 고객 노출 문장 블록 한 건 — 엔진 {@code final_explanation.blocks} 의
	 * {@code block_code·block_title·text·evidence_refs} 만 나른다. {@code source_systems} 는
	 * 내부 조회 계보라 계약에 싣지 않는다(고객 비노출 경계).
	 */
	record ResultBlock(String code, String title, String text, List<String> evidenceRefs) {
	}

	/**
	 * 설명실행이 실제 사용한 근거 한 건. {@code evidenceType} 은 {@link EvidenceType} 코드 —
	 * 현행 소스는 문서 lineage 뿐이라 공시·뉴스만 나오지만, 비문서 근거(ALPHA-878 C1)가
	 * 붙어도 같은 행 골격을 쓴다. 한글 라벨은 UI 뷰 계층 소관(근거 포맷 명세 §10.3).
	 */
	record EvidenceRow(String evidenceType, String title, String sourceCode,
			OffsetDateTime publishedAt) {
	}

	/**
	 * 근거 유형 — <b>선언 순서가 곧 표시 순서</b>다(근거 포맷 명세 §1: 가격→구성종목→공시→
	 * 뉴스→재무및컨센서스→통계검정). 정렬 SQL 의 CASE 가 이 ordinal 로 생성되므로 순서를
	 * 바꾸면 화면 정렬이 바뀐다.
	 */
	enum EvidenceType {
		PRICE, HOLDING, DISCLOSURE, NEWS, FINANCIAL, STAT_TEST;

		/** 유형 고정 순서를 SQL CASE 로 편다. 선언에 없는 미지 코드는 맨 뒤(정렬을 안 흔든다). */
		static String rankCase(String column) {
			StringBuilder sql = new StringBuilder("CASE ").append(column);
			for (EvidenceType type : values()) {
				sql.append(" WHEN '").append(type.name()).append("' THEN ").append(type.ordinal());
			}
			return sql.append(" ELSE ").append(values().length).append(" END").toString();
		}
	}
}
