/* 산출 체인 — 트리거에서 전달까지 (ALPHA-738).
 *
 * 이 흐름을 "배치"라고 부르지 않는다 — 설명 생산 흐름이다. 데이터셋별 수집→정제→적재가 배치고,
 * 그건 데이터셋 화면 소관이다.
 */
import { Link } from 'react-router-dom';
import { StatusBadge } from 'ui-kit';
import { ChainStrip } from './ChainStrip';
import { intradayLostAtEntry, pendingTriggers, stagesBlind } from './chainView';
import { STATE_LABEL, etfTable, rowTone, stateTone, type StateTone } from './etfAnalysisView';
import { Absent, AxisHeader, ConsoleGate, Info, useConsoleFactsQuery, useFocusRow } from './shared';
import '../../styles/ops.css';

const TONE_COLOR: Record<StateTone, string> = {
  bad: 'var(--down)',
  wait: 'var(--fg-2)',
  ok: 'var(--up)',
  none: 'var(--fg-4)',
};

export function ChainPage() {
  const q = useConsoleFactsQuery();
  useFocusRow(q.ready);
  if (!q.ready) return <ConsoleGate q={q} />;
  const { queues } = q.facts;
  const table = etfTable(q.facts.etf_analyses);

  return (
    <div className="flex flex-col gap-4">
      <AxisHeader
        q={q}
        question="트리거된 설명이 Cloud 게시까지 갔고, 어느 단계에서 사라졌는가?"
        note="Cloud Event Store 적재·게시 확정까지가 이 화면의 범위입니다"
      />

      <div className="card">
        <div className="card-head">
          <span className="t-label">단계별 잔존 — 트리거 → Cloud 게시</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            각 칸은 배치 / 장중
            <Info
              tip={
                '단계는 새 분석 엔진의 것이다: 분석 요청 → 분석 시작 → 분석 완료 → 게시.\n' +
                '각 수는 조회일에 발화한 장중 트리거 가운데 그 단계에 도달한 트리거 수다(그날 만들어진 분석 전체가 아니다).\n\n' +
                '배치 트리거는 새 분석 엔진이 소비하지 않아 뒤 단계의 배치 칸은 "대상 아님"이다.\n\n' +
                '손실로 판정하는 구간(R10)은 트리거 → 분석 요청, 분석 요청 → 분석 시작이다. ' +
                '분석 시작 → 완료의 감소는 실패했거나 진행 중인 분석이고(실패는 R15 가 맡는다), ' +
                '완료 → 게시의 감소는 고른 항목이 없어 게시하지 않은 분석이다 — 둘 다 수만 보여 준다.'
              }
              label="체인 구조"
            />
          </span>
        </div>
        {stagesBlind(q.facts.chain) && (
          <p className="card-pad t-xs m-0" style={{ color: 'var(--warn)', paddingBottom: 0 }}>
            이 날에는 새 분석 엔진의 요청이 아직 없었습니다 — 트리거 뒤 단계는 0건이 아니라 관측
            불가입니다. 옛 설명 엔진이 돌던 때의 값은 여기서 다시 보여 주지 않습니다. 구간 손실(R10)도
            같은 이유로 판정하지 못합니다.
          </p>
        )}
        {/* 단계가 관측 불가인 날에는 띄우지 않는다 — 유예가 지나도 그 단계들은 판정하지 못한다 */}
        {!stagesBlind(q.facts.chain) && pendingTriggers(q.facts.chain) > 0 && (
          <p className="card-pad t-xs m-0" style={{ color: 'var(--fg-3)', paddingBottom: 0 }}>
            진행 중일 수 있어 아직 세지 않은 트리거 {pendingTriggers(q.facts.chain)}건 — 앞 단계에 도달한 지
            유예 시간(단일 분석 워크플로의 시간 제한)이 안 지났습니다. 그 뒤에도 다음 단계에 못 가면
            손실로 셉니다.
          </p>
        )}
        <ChainStrip chain={q.facts.chain} />
        <div className="card-pad" style={{ paddingTop: 0 }}>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            {/* 🔴 이 문장은 **관측 결과**다 — 축이 있는지가 아니라 **이번 응답의 값**이 정한다.
                축이 오기 전에는 "축이 있으면 참"이었지만(목 스냅샷이 늘 0 이었다) 이제 실 원장이
                오므로, 조건을 값에서 다시 세운다: 장중이 발화했는데 첫 단계가 0 인 날에만 참이다.
                안 그러면 장중 계보가 살아난 날 화면이 "전량 사라진다"를 계속 단정한다. */}
            {intradayLostAtEntry(q.facts.chain) && (
              <>
                장중 트리거가 분석 요청으로 하나도 이어지지 않았습니다 — 분석이 실패한 것이 아니라
                요청이 만들어지지 않은 것입니다.{' '}
              </>
            )}
            이 흐름은 <b>Cloud 게시</b>에서 끝납니다 — 발번 이후는{' '}
            <Link to="/ops/delivery">Cloud 게시·발번 경계</Link>가 답합니다.
          </p>
        </div>
      </div>

      <div className="card" id="etf-ledger">
        <div className="card-head">
          <span className="t-label">ETF별 분석 귀결</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            {table ? table.summary : '기록 없음'}
            <Info
              tip={
                table
                  ? '새 분석 엔진의 조회일 상태다. ETF 마다 전망과 가격변동 분석을 따로 보여 준다.\n' +
                    '그날 분석 행이 있거나 장중 트리거가 발화한 ETF 만 나온다.\n\n' +
                    '완료: 그날 완료된 분석이 있다.\n' +
                    '실패: 실패한 분석이 있고 완료된 분석이 없다(R15 와 같은 기준).\n' +
                    '진행 중: 진행 중인 분석만 있다.\n' +
                    '시작 못 함: 트리거에서 이어진 요청이 수용됐는데 분석이 시작되지 않았다.\n' +
                    '판정 보류: 같은 형상인데 유예 시간 안이라 아직 판정하지 않았다.\n' +
                    '대상 아님: 위 어느 것도 아니다. 가격변동 분석은 그날 요청으로 이어진 트리거가 없던 ETF 다. ' +
                    '전망은 그날 분석 행이 없는 ETF 인데, 배치 대상인데 빠진 것인지는 이 표가 가르지 못한다.\n\n' +
                    '이 표는 보여 주기만 한다. 판정은 R15(분석 실패)와 R10(구간 손실)이 한다.'
                  : '이 응답에는 새 분석 엔진의 ETF별 상태가 없다 — 그 축을 싣기 전의 API 다. ' +
                    '옛 설명 엔진의 실행 상태로 대신 그리지 않는다.'
              }
              label="ETF별 분석 귀결"
            />
          </span>
        </div>
        {/* ⛔ 축이 없을 때 빈 격자를 그리지 않는다 — 칸이 0개인 격자는 "오늘 실패한 ETF 가 없다"로
            읽히는데, 이 응답은 그 말을 할 근거가 없다(계약 §부재를 싣는 규약). 옛 축으로도 그리지
            않는다 — 그 엔진은 멈췄고 그 값은 전부 "실행 없음"이다. */}
        {!table ? (
          <div className="card-pad">
            <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
              기록 없음 — 이 응답에는 새 분석 엔진의 ETF별 상태가 실려 있지 않아 이 표를 그리지
              못합니다.
            </p>
          </div>
        ) : (
        <div className="card-pad">
          <div className="ops-grid-etf">
            {table.rows.map((r) => {
              const tone = rowTone(r);
              return (
                <div
                  key={r.etf}
                  className={
                    'ops-etf' + (tone === 'bad' ? ' ops-etf-failed' : tone === 'ok' ? ' ops-etf-published' : '')
                  }
                  title={
                    `${r.name} (${r.etf})\n` +
                    `전망: ${STATE_LABEL[r.outlook]}\n` +
                    `가격변동 분석: ${STATE_LABEL[r.movement]}`
                  }
                >
                  <div className="t-xs" style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {r.name}
                  </div>
                  {/* 종류마다 그 ETF 의 그날 분석 목록으로 간다(ALPHA-1333). 날짜는 이 응답이 본 날이다 */}
                  {([['전망', 'outlook', r.outlook], ['가격변동', 'movement', r.movement]] as const).map(
                    ([label, kind, state]) => (
                      <Link
                        key={kind}
                        to={`/analysis-results?kind=${kind}&etf=${encodeURIComponent(r.etf)}&from=${q.facts.meta.today}`}
                        className="t-xs block"
                        style={{ color: TONE_COLOR[stateTone(state)] }}
                      >
                        {label} {STATE_LABEL[state]}
                      </Link>
                    ),
                  )}
                </div>
              );
            })}
          </div>
        </div>
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">큐 · 구독 워커 (Cloud)</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            대기가 있는데 구독 서비스가 없으면 런타임 실패가 아니라 배선 부재입니다
          </span>
        </div>
        {/* ⛔ `queues ?? []` 로 빈 표를 그리지 않는다 — 행 0개는 "큐가 0개"라는 거짓말이고,
            축 부재는 AWS 제어면 미배선(C)이지 큐가 없다는 관측이 아니다.
            ⚠️ 지금 도달 가능한 부재는 **필드 부재(미배선) 하나**다 — 계약의 두 번째 형상
            (`queues: null` + `awsUnavailable` = 조회했는데 못 봄 = 관측 불가)은 C 가 붙어야
            생긴다. 그때 이 자리도 두 갈래로 갈라야 한다(한 칸에 그리면 제어면 장애가
            "아직 계측이 없구나"로 읽힌다 — `meta.aws` 에서 두 라운드 겪은 그 방향). */}
        {!queues ? (
          <div className="card-pad">
            <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
              큐 상태는 <Absent kind="uninstrumented" /> 입니다 — SQS 제어면 조회가 아직
              배선되지 않아 대기·처리 중·DLQ 를 <b>물어본 적이 없습니다</b>. 큐가 0개라는 뜻이
              아니고, R12 도 같은 이유로 판정하지 못합니다.
            </p>
          </div>
        ) : (
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
              {queues.map((queue) => (
                <tr key={queue.name} id={'q-' + queue.name}>
                  <td className="mono">{queue.name}</td>
                  <td className="col-muted">{queue.purpose ?? <Absent kind="uninstrumented" />}</td>
                  <td
                    className="num"
                    style={queue.visible ? { color: 'var(--down)', fontWeight: 600 } : undefined}
                  >
                    {queue.visible}
                  </td>
                  <td className="num">{queue.in_flight}</td>
                  <td className="num">{queue.dlq}</td>
                  <td>
                    {queue.subscribers == null ? (
                      <Absent kind="uninstrumented" />
                    ) : queue.subscribers.length ? (
                      queue.subscribers.map((s) => (
                        <span key={s} className="chip" style={{ marginRight: 4 }}>
                          {s}
                        </span>
                      ))
                    ) : (
                      <StatusBadge tone="blocked">없음</StatusBadge>
                    )}
                    {queue.sub_mock && <span className="chip">MOCK</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="card-pad" style={{ paddingTop: 0 }}>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            큐→서비스 매핑이 어디에도 선언돼 있지 않아 "장중 트리거 처리 워커 미배포"를 사람이 판단하고
            있습니다. 이 매핑이 생기면 R11 이 자동으로 돕니다. 여기서 말하는 워커는 <b>Cloud 안에서 큐를
            처리하는 실행체</b>이지, 발번 이후 온프렘에서 데이터를 받는 테넌트 소비자가 아닙니다.
          </p>
        </div>
      </div>
    </div>
  );
}
