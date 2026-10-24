package com.edge.superadmin.repository;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

/**
 * 런의 <b>AWS 제어면 관측</b>(ALPHA-979 조각 2) — Step Functions 가 지금 그 실행을 뭐라고 하는가.
 *
 * <p>🔴 <b>원장과 합치지 않는다.</b> 콘솔은 DB 원장 투영({@code orchestration_status})과 이 값을
 * <b>별도 축</b>으로 그리고 어느 쪽도 상대를 덮지 않는다({@code runObservation.ts} 가 그 규약의
 * 정본). 원장 값으로 폴백하면 대조가 무의미해진다 — 못 받았으면 <b>없는 것</b>이다.
 *
 * <p>🔴 <b>어휘를 다시 정의하지 않는다.</b> SFN 이 준 문자열을 그대로 올린다. 모르는 값도
 * 마찬가지다 — 화면이 "미지원 상태 · RAW" 로 크게 드러낸다(fail loud). 여기서 성공·실패로
 * 접으면 그 판정이 서버로 새고, 새 enum 이 조용히 기존 칸으로 흡수된다.
 */
public interface RunControlPlane {

	/**
	 * 실행 ARN 들의 현재 상태. <b>실패는 예외가 아니라 {@link Observation#unavailable()} 이다</b> —
	 * 제어면을 못 봤다고 원장 축까지 못 보면 안 된다(ADR-0050: AWS 실패가 DB 축을 막지 않는다).
	 *
	 * <p>돌려주는 맵은 <b>본 것만</b> 담는다. 못 본 ARN 은 키가 없고, 그 구분이 화면의
	 * "AWS 관측 없음"이 된다 — 빈 값으로 채우면 관측 실패가 "실행이 없다"로 뒤집힌다.
	 */
	Observation describe(List<String> executionArns);

	/**
	 * 한 번의 관측 결과.
	 *
	 * @param at      관측 시각. <b>{@code null} 이면 제어면을 아예 못 봤다</b>(자격증명·권한·장애).
	 *                화면은 그걸 "조회했는데 못 봤다"로 그리고, 키 자체가 없는 것(미배선)과 다르게 센다.
	 * @param byArn   본 실행만. 키가 없다 = 그 런은 관측 못 했다.
	 */
	record Observation(OffsetDateTime at, Map<String, RunState> byArn) {

		public static Observation unavailable() {
			return new Observation(null, Map.of());
		}

		public boolean observed() {
			return at != null;
		}
	}

	/**
	 * 실행 하나의 제어면 상태.
	 *
	 * @param status SFN 원문(RUNNING·SUCCEEDED·FAILED·TIMED_OUT·ABORTED·PENDING_REDRIVE 등).
	 *               <b>정규화하지 않는다.</b>
	 * @param stopAt 종료 시각. 진행 중이면 {@code null}. 소비자는 이 값으로 <b>투영 지연</b>
	 *               (원장이 아직 못 따라옴)과 <b>진짜 불일치</b>를 가른다 — 원장 갱신이 이 시각보다
	 *               이르면 원장은 아직 종료를 볼 기회가 없었던 것이다.
	 */
	record RunState(String status, OffsetDateTime stopAt) {
	}
}
