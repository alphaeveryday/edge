/* 뉴스 처리 퍼널 — **응답 밖 축이라 앱 번들 안 스냅샷이 유일한 출처다** (ALPHA-738 D).
 *
 * `news_funnel` 은 `/api/v1/console/facts` 에 없다(계약 §「범위 결정」): 규칙 입력이 0건이고,
 * 필드 대부분(`maxPages`·`dedupKey`·`timestampAxis`·`universeFilter`)이 그날의 관측이 아니라
 * **수집 코드의 성질**이다. 뉴스 집계를 주는 표면은 따로 있다(`/api/v1/sources/lineage/news`).
 *
 * 🔴 **그래서 이 축은 알려진 부채다.** 다른 축이 실 응답으로 선 뒤에도 여기만 스냅샷이라,
 * 한 화면에 **두 날짜**가 선다(스냅샷 `DATE` vs 응답의 `meta.today`). 읽는 쪽은 그 사실을
 * 반드시 화면에 낸다 — 안 내면 운영자가 오늘 값으로 읽는다. 날짜를 밖에서 주지 않고 여기서
 * 함께 내보내는 이유가 그것이다(값과 그 값이 언제 것인지는 갈리면 안 된다).
 *
 * ⚠️ JSX 를 쓰지 않는다 — `node --test` 가 import 해야 `trendCatalog` 의 변이가 잡힌다.
 */
import factsJson from '../../rules/facts-snapshot.json' with { type: 'json' };

/**
 * 퍼널 단계.
 *
 * ⚠️ 주의사항은 **완성된 문장이 아니라 구조화된 필드**다 — 화면이 그 값으로 배지·설명을
 * 결정적으로 만든다. 문장을 데이터에 박아 두면 표 셀이 문단이 되고, 값이 바뀌어도 문장은
 * 그대로 남는다(예: 상한 도달 런이 0인데 "상한 절단값" 문구가 남는 일).
 */
export interface FunnelStep {
  stage: string;
  value: number;
  unit: string;
  /** 창 겹침이 값에 포함되는가 */
  includesWindowOverlap?: boolean;
  /** 중복이 값에 포함되는가 */
  includesDuplicates?: boolean;
  /** 런당 수집 상한 = maxPages × pageSize */
  maxPages?: number;
  pageSize?: number;
  totalRuns?: number;
  /** 상한에 도달한 런 수 — 0 보다 크면 "수집 상한 도달" */
  runsAtLimit?: number;
  dedupKey?: string;
  /** 앞 단계와 시각 축이 다르면 값이 어긋나는 게 정상이다 */
  timestampAxis?: string;
  /** 유니버스 필터가 걸리는 단계인가 */
  universeFilter?: boolean;
  /** 실질 탈락 단계인가 */
  dropStage?: boolean;
  /** 부재 사유 계측이 있는가. false = 사유가 한 통이라 원인을 못 가른다 */
  missingReasonInstrumentation?: boolean;
  /** 구조화할 수 없는 짧은 보충만 남긴다 */
  note?: string;
}

const SNAPSHOT = factsJson as unknown as {
  news_funnel: FunnelStep[];
  meta: { today: string };
};

export const FUNNEL: FunnelStep[] = SNAPSHOT.news_funnel;

/** 이 퍼널이 관측된 날 — **응답의 거래일이 아니다.** 화면·지표가 이 날짜를 그대로 밝힌다 */
export const FUNNEL_DATE: string = SNAPSHOT.meta.today;

/** 스냅샷 출처를 한 문장으로 — 화면과 추이 지표가 **같은 문장**을 쓴다(두 벌이면 갈린다) */
export const FUNNEL_ORIGIN =
  `뉴스 퍼널은 이 응답 밖 축이라 앱에 동봉된 ${FUNNEL_DATE} 스냅샷입니다 — ` +
  `같은 화면의 다른 값과 날짜가 다를 수 있고, 오늘의 관측이 아닙니다.`;

/** 단계 접두사로 값 하나 — 없으면 `null`(0 이 아니다) */
export const funnelValue = (stage: string): number | null =>
  FUNNEL.find((s) => s.stage.startsWith(stage))?.value ?? null;
