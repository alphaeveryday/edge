/* 산출 체인 — 트리거에서 전달까지 (ALPHA-738).
 *
 * 이 흐름을 "배치"라고 부르지 않는다 — 설명 생산 흐름이다. 데이터셋별 수집→정제→적재가 배치고,
 * 그건 데이터셋 화면 소관이다.
 */
import { StatusBadge } from 'ui-kit';
import { ChainStrip } from './ChainStrip';
import { Absent, AxisHeader, F, Info, useFocusRow } from './shared';
import '../../styles/ops.css';

export function ChainPage() {
  useFocusRow();
  const rows = F.etf_ledger?.rows ?? [];
  const failed = rows.filter((r) => r.outcome === 'FAILED');
  const published = rows.filter((r) => r.published);
  const queues = F.queues ?? [];

  return (
    <div className="flex flex-col gap-4">
      <AxisHeader question="트리거된 설명이 어디까지 갔고, 어느 단계에서 사라졌는가?" />

      <div className="card">
        <div className="card-head">
          <span className="t-label">단계별 잔존</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            각 칸은 배치 / 장중
            <Info
              tip={
                '체인은 하나다.\n배치 트리거와 장중 트리거는 별개 흐름이 아니라 같은 체인에 들어오는 두 갈래 입력이다 — ' +
                'etf_contribution_observation 이 두 트리거 FK 를 모두 갖는다.'
              }
            />
          </span>
        </div>
        <ChainStrip />
        <div className="card-pad" style={{ paddingTop: 0 }}>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            장중 갈래는 관측 단계에서 전량이 사라집니다 — 체인에서 실패한 것이 아니라 체인에 들어가지 못한 것입니다.
            마지막 단계(소비자 수신)는 관측 채널이 없어 <b>관측 불가</b>이며, 0이 아닙니다.
          </p>
        </div>
      </div>

      <div className="card" id="etf-ledger">
        <div className="card-head">
          <span className="t-label">
            ETF별 분석 귀결 <span className="chip">MOCK</span>
          </span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            {rows.length}종 · 게시 {published.length} / 실패 {failed.length} / 트리거 없음{' '}
            {rows.filter((r) => !r.triggered).length}
            <Info
              tip={
                (F.etf_ledger?.why ?? '') +
                `\n\n이 원장이 있으면 "설명 미생성 ${failed.length}종이 어느 ETF인지"에 답할 수 있고, R15 가 대상을 지목한다. ` +
                '지금은 AnalyzeOne 이 ETF 단위 귀결·오류를 남기지 않아 목값이다.'
              }
            />
          </span>
        </div>
        <div className="card-pad">
          <div className="ops-grid-etf">
            {rows.map((r) => (
              <div
                key={r.etf}
                className={
                  'ops-etf' +
                  (r.outcome === 'FAILED' ? ' ops-etf-failed' : r.published ? ' ops-etf-published' : '')
                }
                title={
                  `${r.name} (${r.etf})\n` +
                  `트리거: ${r.triggered ? '발화' : '미발화(정상 변동)'}\n` +
                  `귀결: ${r.outcome}` +
                  (r.error ? `\n오류: ${r.error}` : '') +
                  `\n게시: ${r.published ? 'PUBLISHED' : '—'} · 전달: ${r.delivered ? 'NEW' : '—'}`
                }
              >
                <div className="t-xs" style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {r.name}
                </div>
                <div
                  className="t-xs"
                  style={{
                    color:
                      r.outcome === 'FAILED' ? 'var(--down)' : r.published ? 'var(--up)' : 'var(--fg-4)',
                  }}
                >
                  {r.outcome === 'FAILED' ? '분석 실패' : r.published ? '게시' : '트리거 없음'}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">큐 · 구독 서비스</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            대기가 있는데 구독 서비스가 없으면 런타임 실패가 아니라 배선 부재입니다
          </span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>큐</th>
              <th>용도</th>
              <th className="num">대기</th>
              <th className="num">처리 중</th>
              <th className="num">DLQ</th>
              <th>구독 서비스</th>
            </tr>
          </thead>
          <tbody>
            {queues.map((q) => (
              <tr key={q.name} id={'q-' + q.name}>
                <td className="mono">{q.name}</td>
                <td className="col-muted">{q.purpose}</td>
                <td className="num" style={q.visible ? { color: 'var(--down)', fontWeight: 600 } : undefined}>
                  {q.visible}
                </td>
                <td className="num">{q.in_flight}</td>
                <td className="num">{q.dlq}</td>
                <td>
                  {q.subscribers == null ? (
                    <Absent kind="uninstrumented" />
                  ) : q.subscribers.length ? (
                    q.subscribers.map((s) => (
                      <span key={s} className="chip" style={{ marginRight: 4 }}>
                        {s}
                      </span>
                    ))
                  ) : (
                    <StatusBadge tone="blocked">없음</StatusBadge>
                  )}
                  {q.sub_mock && <span className="chip">MOCK</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="card-pad" style={{ paddingTop: 0 }}>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            큐→서비스 매핑이 어디에도 선언돼 있지 않아 "소비자 미배포"를 사람이 판단하고 있습니다. 이 매핑이 생기면
            R11 이 자동으로 돕니다.
          </p>
        </div>
      </div>
    </div>
  );
}
