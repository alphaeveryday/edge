/* 추이 카드의 **as-of 표기** (ALPHA-738 D).
 *
 * ⚠️ JSX 를 쓰지 않는다 — `TrendPage.tsx` 안에 있던 동안은 `node --test` 가 import 을 못 해
 * 이 판단의 변이가 하나도 안 잡혔다(`notRun`·`datasetFreshness` 를 내린 것과 같은 이유).
 */
import type { Metric } from './trendMetrics.ts';

/**
 * 마지막 점이 **언제 것인가**. 지표마다 다르다 — 하루 facts 지표는 조회일이고, 별도 일별 API의
 * 실측은 마지막 관측일이며, 뉴스 퍼널은 응답 밖 축이라 **스냅샷 날**이다(계약 §범위 결정).
 *
 * 🔴 전부 `오늘` 이라 쓰면 과거 스냅샷 값이 조회일 관측으로 읽힌다 — 도움말을 열지 않은
 * 사용자와 스크린리더 사용자에게는 그게 화면의 전부다.
 * 🔴 계열이 아예 없으면 **관측 시각도 없다** — `null` 이다. `오늘` 로 떨어뜨리면 화면에
 * `오늘 —` 이 서서 "오늘 조회했는데 값이 없다"와 "이 축을 아무도 안 셌다"가 같은 칸이 된다.
 *
 * ⚠️ 여기서 `관측 없음` 같은 **새 문구를 만들지 않는다.** 이 콘솔의 부재 어휘는 넷이고
 * (`0`·`—`·`관측 불가`·`계측 없음`) 다섯째를 들이면 그 넷의 뜻이 흐려진다. 그 카드는 이미
 * `계측 없음` 배지와 `계열 없음` 그래프로 부재를 말하고 있으므로, 여기서는 **날짜를 안 붙인다**.
 */
export function asOfLabel(metric: Metric, today: string): string | null {
  const last = metric.series[metric.series.length - 1]?.date;
  if (last === undefined) return null;
  return last === today ? '오늘' : last;
}
