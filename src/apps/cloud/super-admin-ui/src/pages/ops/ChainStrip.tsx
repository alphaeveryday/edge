/* 설명 생산 체인 스트립 — 홈과 산출 체인 화면이 공유한다 (ALPHA-738).
 *
 * 체인은 하나다. 배치 트리거와 장중 트리거는 별개 파이프라인이 아니라 같은 체인의 두 입력이라
 * (etf_contribution_observation 이 두 트리거 FK 를 모두 갖는다) 단계마다 배치/장중을 나란히 낸다.
 */
import { F, fmt } from './shared';

export function ChainStrip() {
  const S = F.chain.stages;
  return (
    <div className="ops-chain">
      <div className="ops-feeds">
        {F.chain.feeds.map((f, i) => (
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
        const prevB = i === 0 ? F.chain.feeds[0]?.v : S[i - 1].batch;
        const prevI = i === 0 ? F.chain.feeds[1]?.v : S[i - 1].intraday;
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
                `${s.label}\n출처: ${s.src}` +
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
                {s.label}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
