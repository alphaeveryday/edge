/* 체인 화면의 판정 — **`.tsx` 에 두지 않는다**(ALPHA-979 조각 1).
 *
 * `node --test` 의 글롭은 `src/**\/*.test.ts` 라 `.tsx` 를 안 집는다. 화면 파일 안에 판정을 두면
 * 변이를 걸어도 전건 통과한다 — 이 레포가 세 번 겪고 규약으로 박은 자리다(README 모듈 표).
 */
import type { Facts } from '../../rules/types';

/**
 * 장중 갈래가 **입구에서** 통째로 사라졌는가 — 발화는 있었는데 관측이 하나도 안 만들어진 상태.
 *
 * 🔴 **이것은 관측 결과이지 축의 유무가 아니다.** 축이 목 스냅샷이던 동안은 장중이 늘 0 이라
 * "축이 있으면 참"이 우연히 맞았다. 실 원장이 오는 지금 그 조건을 그대로 두면, 장중 계보가
 * 살아난 날 화면이 <b>이번 응답이 하지 않은 관측</b>을 계속 단정한다.
 *
 * 발화가 0 인 날은 <b>거짓</b>이다 — 사라질 것이 없었던 날을 "전량 사라졌다"고 말하면
 * 조용한 날과 막힌 날이 같은 문장을 받는다.
 */
export function intradayLostAtEntry(chain: Facts['chain']): boolean {
  if (!chain) return false;
  const fired = chain.feeds[1]?.v;
  /* 첫 단계가 곧 입구다 — id 로 찾지 않는다. 순서가 흐름이라는 계약을 여기서도 그대로 쓴다. */
  const observed = chain.stages[0]?.intraday;
  return Number.isFinite(fired) && (fired as number) > 0 && observed === 0;
}
