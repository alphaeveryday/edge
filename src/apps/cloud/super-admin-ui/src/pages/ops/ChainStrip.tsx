/* 설명 생성 흐름 스트립 (ALPHA-738).
 *
 * 단계는 새 분석 엔진의 것이다(ALPHA-1296): 분석 요청 → 분석 시작 → 분석 완료 → 게시. 수는
 * 조회일에 발화한 장중 트리거 가운데 그 단계에 도달한 트리거 수다. 입력은 두 갈래(배치·장중
 * 트리거)를 그대로 그리지만, 배치 트리거는 새 분석 엔진이 소비하지 않아 뒤 단계의 배치 칸은
 * "대상 아님"이다(0 이 아니다).
 *
 * **어디서 끝나는가**: Cloud 게시까지다. tenant_delivery 발번은 Cloud→테넌트 전달 경계이고
 * 그 뒤(Sync Agent·Intake·Screening·온프렘 최종 게시·소비자 노출)는 온프렘 영역이라
 * 이 화면의 책임이 아니다(ADR-0026). 여기서 이어 그리면 경계가 흐려진다.
 *
 * 원본 사실(facts-snapshot·DB 컬럼·enum)은 건드리지 않는다 — **표시 계층에서만** 자르고
 * 라벨을 붙인다. 규칙 R10 은 전체 stages 를 그대로 읽는다.
 */
import type { Facts } from '../../rules/types';
import { Absent, fmt } from './shared';

/** 이 화면이 그리는 마지막 단계 — 이후는 전달 경계·온프렘 소관 */
const LAST_STAGE_ID = 'c.pub';
/** 표시 라벨만 바꾼다. 원장의 '게시'는 Cloud Event Store 의 게시 상태이지 온프렘 최종 게시가 아니다 */
const DISPLAY_LABEL: Record<string, string> = { 'c.pub': 'Cloud 게시' };

export function ChainStrip({ chain }: { chain: Facts['chain'] }) {
  /* 축이 통째로 없으면 **빈 띠를 그리지 않는다** — 단계가 0개인 흐름은 "아무것도 안 흘렀다"로
   * 읽히는데, 사실은 이 응답이 그 축을 안 실은 것이다(계약 §체인 축). 축은 배선돼 있으므로
   * (ALPHA-979 조각 1) 여기 도달하는 것은 그 축을 안 싣던 배포본을 보고 있다는 뜻이다. */
  if (!chain) {
    return (
      <div className="ops-chain-absent t-xs" style={{ color: 'var(--fg-3)' }}>
        흐름 단계 집계가 이번 응답에 <Absent kind="uninstrumented" /> — 단계별 건수가 오지
        않았습니다. 구간 손실(R10)도 같은 이유로 판정하지 못합니다.
      </div>
    );
  }
  /* 가드 뒤에 **값으로 꺼낸다** — 콜백 안에서는 프로퍼티 좁히기가 풀려 `!` 를 다시 쓰게 된다.
   * 그 `!` 가 축이 옵셔널이 되는 날 조용히 죽는 자리다(계약이 `trendCatalog` 에서 지목한 패턴). */
  const { feeds, stages: all } = chain;
  const end = all.findIndex((s) => s.id === LAST_STAGE_ID);
  const S = end >= 0 ? all.slice(0, end + 1) : all;
  return (
    <div className="ops-chain">
      <div className="ops-feeds">
        {feeds.map((f, i) => (
          <div
            key={f.id}
            className={'ops-feed ' + (i === 0 ? 'ops-feed-batch' : 'ops-feed-intraday')}
            title={`${f.label}\n단위: ${f.unit}\n출처: ${f.src}${f.note ? `\n${f.note}` : ''}`}
          >
            <div className="ops-stage-n">{fmt(f.v)}</div>
            <div className="t-xs" style={{ color: 'var(--fg-3)' }}>
              {f.label}
            </div>
          </div>
        ))}
      </div>
      {S.map((s, i) => {
        const prevB = i === 0 ? feeds[0]?.v : S[i - 1].batch;
        const prevI = i === 0 ? feeds[1]?.v : S[i - 1].intraday;
        /* 관측 불가 단계에는 감소분을 그리지 않는다 — 그 0 은 실측이 아니라, 빼면 없는 손실이 선다 */
        const lostB = !s.blind && prevB != null && s.batch != null && s.batch < prevB ? prevB - s.batch : 0;
        const lostI = !s.blind && prevI != null && s.intraday != null && s.intraday < prevI ? prevI - s.intraday : 0;
        return (
          <div key={s.id} style={{ display: 'contents' }}>
            {/* 감소분은 그 손실이 난 단계 **앞**에 둔다 — 뒤에 두면 어느 단계에서 사라졌는지 오독된다 */}
            <div className={'ops-gap' + (lostB || lostI ? '' : ' ops-gap-none')}>
              {lostB || lostI ? (
                <>
                  {lostB > 0 && <div>−{lostB}</div>}
                  {lostI > 0 && <div style={{ color: 'var(--warn)' }}>−{lostI}</div>}
                </>
              ) : (
                '·'
              )}
            </div>
            <div
              className="ops-stage"
              id={'chain-' + s.id}
              title={
                `${DISPLAY_LABEL[s.id] ?? s.label}\n출처: ${s.src}` +
                (s.note ? `\n${s.note}` : '') +
                (s.blind ? '\n관측 불가 — 이 단계를 셀 쪽이 없다(0이 아니다)' : '') +
                (s.batch_na ? '\n배치 칸은 대상 아님 — 배치 트리거는 새 분석 엔진이 소비하지 않는다' : '') +
                (s.shown_only ? '\n이 단계로의 감소는 손실로 판정하지 않는다(R10 대상 아님)' : '')
              }
            >
              {s.blind ? (
                <div className="ops-stage-n" style={{ color: 'var(--fg-4)', fontSize: 13 }}>
                  관측 불가
                </div>
              ) : (
                <div
                  className="ops-stage-n"
                  style={(s.batch_na || s.batch === 0) && s.intraday === 0 ? { color: 'var(--down)' } : undefined}
                >
                  {/* 배치 갈래가 대상이 아닌 단계는 0 도 — 도 아니다 — 그 사실을 그대로 적는다 */}
                  {s.batch_na ? <span style={{ color: 'var(--fg-4)', fontSize: 13 }}>대상 아님</span> : fmt(s.batch)}
                  <span className="sub"> / {fmt(s.intraday)}</span>
                </div>
              )}
              <div className="t-xs" style={{ color: 'var(--fg-3)' }}>
                {DISPLAY_LABEL[s.id] ?? s.label}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
