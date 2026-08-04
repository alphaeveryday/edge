/* 산출 추이 — 오늘 값이 평소 분포 안에 있는가 (ALPHA-738).
 *
 * 이 화면은 이상을 지목하기만 하고 원인은 말하지 않는다 — 원인은 다른 규칙이 지목한다.
 */
import { StatusBadge } from 'ui-kit';
import { Absent, AxisHeader, F, Info, fmt, useFocusRow } from './shared';
import '../../styles/ops.css';

export function TrendPage() {
  useFocusRow();
  const funnel = F.news_funnel;

  return (
    <div className="flex flex-col gap-4">
      <AxisHeader question="오늘 산출량이 평소와 얼마나 다른가?" />

      <div className="card">
        <div className="card-head">
          <span className="t-label">산출 델타</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            직전 10영업일 중앙값 대비 ±25% 이상이면 분포 밖 (R13)
            <Info tip={'기준선은 평균이 아니라 중앙값이다 — 하루짜리 장애가 기준선을 끌고 가지 않게.'} label="기준선" />
          </span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>지표</th>
              <th className="num">오늘</th>
              <th className="num">평소(중앙값)</th>
              <th className="num">편차</th>
              <th>판정</th>
            </tr>
          </thead>
          <tbody>
            {F.outputs.map((o) => {
              const pct = o.base ? Math.round(((o.today - o.base) / o.base) * 100) : null;
              const out = pct !== null && Math.abs(pct) >= 25;
              return (
                <tr key={o.id} id={'out-' + o.id}>
                  <td>{o.label}</td>
                  <td className="num">
                    {fmt(o.today)} <span className="col-muted t-xs">{o.unit}</span>
                  </td>
                  <td className="num col-muted">{o.base != null ? fmt(o.base) : <Absent kind="none" />}</td>
                  <td className="num" style={out ? { color: 'var(--down)', fontWeight: 600 } : undefined}>
                    {pct === null ? <Absent kind="none" /> : `${pct > 0 ? '+' : ''}${pct}%`}
                  </td>
                  <td>
                    {pct === null ? (
                      <span className="t-xs" style={{ color: 'var(--fg-4)' }} title="기준선이 없어 평가 대상이 아니다">
                        기준 없음
                      </span>
                    ) : out ? (
                      <StatusBadge tone="blocked">분포 밖</StatusBadge>
                    ) : (
                      <StatusBadge tone="neutral">정상 범위</StatusBadge>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="card-pad" style={{ paddingTop: 0 }}>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            분포 밖이라는 사실만 말합니다 — 원인은 다른 규칙이 지목합니다.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">뉴스 계보</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            단계마다 단위가 다릅니다 — 빼기 전에 ⓘ 를 보세요
          </span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>단계</th>
              <th className="num">값</th>
              <th>단위</th>
              <th className="num">직전 대비</th>
              <th>주의</th>
            </tr>
          </thead>
          <tbody>
            {funnel.map((x, i) => {
              const prev = i > 0 ? funnel[i - 1].value : null;
              const drop = prev != null && x.value < prev ? prev - x.value : 0;
              return (
                <tr key={x.stage} id={'funnel-' + i}>
                  <td>{x.stage}</td>
                  <td className="num">{fmt(x.value)}</td>
                  <td className="col-muted">{x.unit}</td>
                  <td className="num" style={drop ? { color: 'var(--warn)' } : undefined}>
                    {prev == null ? <Absent kind="none" /> : drop ? `−${fmt(drop)}` : '·'}
                  </td>
                  <td className="col-muted t-xs">{x.note ?? ''}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="card-pad" style={{ paddingTop: 0 }}>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            실질 탈락 단계는 유니버스 매칭입니다 — 앞의 감소는 창 겹침 dedup 과 축 차이라 유실이 아닙니다. 원천 수집의
            런당 4,000 은 기대치가 아니라 MAX_PAGES × 100 절단값입니다.
          </p>
        </div>
      </div>
    </div>
  );
}
