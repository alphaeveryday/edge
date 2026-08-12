/* 설명 생성 흐름 스트립 (ALPHA-738).
 *
 * 체인은 하나다. 배치 트리거와 장중 트리거는 별개 파이프라인이 아니라 같은 체인의 두 입력이라
 * (etf_contribution_observation 이 두 트리거 FK 를 모두 갖는다) 단계마다 배치/장중을 나란히 낸다.
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
   * 읽히는데, 사실은 이 축의 계측이 없는 것이다(계약 §축별 소스: `chain` 은 소스 0). */
  if (!chain) {
    return (
      <div className="ops-chain-absent t-xs" style={{ color: 'var(--fg-3)' }}>
        흐름 단계 집계는 <Absent kind="uninstrumented" /> — 단계별 건수를 세는 계측이 없습니다.
        구간 손실(R10)도 같은 이유로 판정하지 못합니다.
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
        const lostB = prevB != null && s.batch != null && s.batch < prevB ? prevB - s.batch : 0;
        const lostI = prevI != null && s.intraday != null && s.intraday < prevI ? prevI - s.intraday : 0;
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
                (s.blind ? '\n관측 불가 — 클라우드에 소비 확인 채널이 없다(0이 아니다)' : '')
              }
            >
              {s.blind ? (
                <div className="ops-stage-n" style={{ color: 'var(--fg-4)', fontSize: 13 }}>
                  관측 불가
                </div>
              ) : (
                <div
                  className="ops-stage-n"
                  style={s.batch === 0 && s.intraday === 0 ? { color: 'var(--down)' } : undefined}
                >
                  {fmt(s.batch)}
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
