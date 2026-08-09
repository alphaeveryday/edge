/* 데이터셋 신선도 — 여기가 진짜 배치다 (ALPHA-738).
 *
 * 데이터셋마다 수집→정제→적재가 돌고 주기도 다르다. 앞 화면의 체인(트리거→게시)은 배치가 아니라
 * 설명 생산 흐름이고, 이 데이터셋들이 그 재료다.
 */
import { StatusBadge } from 'ui-kit';
import type { TaskFact } from '../../rules/types';
/* 판정은 JSX 밖에 둔다 — 여기 두면 `node --test` 가 import 을 못 해 변이가 하나도 안 잡힌다 */
import { freshness } from './datasetFreshness';
import { Absent, AxisHeader, ConsoleGate, Info, kst, useConsoleFactsQuery, useFocusRow } from './shared';
import { NewsFunnel } from './NewsFunnel';
import '../../styles/ops.css';

export function DatasetPage() {
  useFocusRow();
  const q = useConsoleFactsQuery();
  if (!q.ready) return <ConsoleGate q={q} />;
  const { datasets, tasks } = q.facts;
  const byDataset = new Map<string, TaskFact[]>();
  for (const t of tasks) {
    const key = t.dataset ?? '—';
    if (!byDataset.has(key)) byDataset.set(key, []);
    byDataset.get(key)!.push(t);
  }

  return (
    <div className="flex flex-col gap-4">
      <AxisHeader q={q} question="각 데이터셋이 언제 기준의 사실을 담고 있는가?" />

      <div className="card">
        <div className="card-head">
          <span className="t-label">신선도</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            기대 기준일 대비 실제 기준일
            <Info
              tip={
                'expected_as_of = 계약이 기대하는 기준일, actual_as_of = 데이터가 실제로 담고 있는 기준일.\n' +
                '수집 시각(collected_at)과는 다른 축이다 — 오늘 수집해도 담긴 기준일은 어제일 수 있다.'
              }
              label="기준일 축"
            />
          </span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>데이터셋</th>
              <th>기대 기준일</th>
              <th>실제 기준일</th>
              <th>수집 시각</th>
              <th>판정</th>
              <th>다음 실행</th>
            </tr>
          </thead>
          <tbody>
            {datasets.map((d) => {
              const f = freshness(d);
              return (
                <tr key={d.id} id={'ds-' + d.id}>
                  <td className="mono">
                    {d.id}
                    {d.mock && (
                      <span className="chip" style={{ marginLeft: 6 }}>
                        MOCK
                      </span>
                    )}
                  </td>
                  <td>{d.expected_as_of ?? <Absent kind="none" />}</td>
                  <td>
                    {d.actual_as_of ?? (
                      <span className="t-xs" style={{ color: 'var(--fg-4)' }} title="actual_as_of writer 가 없어 근거 자체가 없다 — 오래됐다는 뜻이 아니다">
                        근거 없음
                      </span>
                    )}
                  </td>
                  <td className="col-muted">{d.collected_at ? kst(d.collected_at) : <Absent kind="none" />}</td>
                  <td>
                    <span title={f.tip}>
                      <StatusBadge tone={f.tone}>{f.label}</StatusBadge>
                    </span>
                  </td>
                  {/* 다음 실행 예정 축은 이 응답에 없다(계약 §축별 소스에 `next_run` 행이 없다).
                      `—`(집계 없음)로 그리면 "이번엔 안 셌다"로 읽힌다 — 안 센 게 아니라 기록이 없다. */}
                  <td className="col-muted">{d.next_run ?? <Absent kind="uninstrumented" />}</td>
                </tr>
              );
            })}
            {datasets.length === 0 && (
              <tr>
                <td colSpan={6} className="t-xs" style={{ color: 'var(--fg-3)' }}>
                  {/* 서버는 **`dataset` 이 비지 않은 모든 작업**에서 데이터셋을 파생한다
                      (계약 유무와 무관). 그러니 빈 목록의 뜻은 "계약이 없다"가 아니라
                      "이 조회 창의 작업이 데이터셋을 지목하지 않았다"까지다. */}
                  이 조회일의 작업이 데이터셋을 하나도 지목하지 않았습니다 — 데이터셋이 없다는
                  뜻이 아니라 <b>작업 행의 dataset 축이 비어 있다</b>는 뜻입니다(계획 자체가
                  없었거나, 원장 writer 가 그 축을 안 채웠거나).
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="card-pad" style={{ paddingTop: 0 }}>
          {/* ⚠️ 계약 건수를 문장에 박지 않는다 — 위 표와 같은 응답에서 센다. 손으로 적으면
              등록이 늘어난 날 표와 문장이 서로 다른 수를 말한다. */}
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            이 응답에서 계약이 연결된 데이터셋은 <b>{datasets.filter((d) => d.contract).length}건</b>
            입니다. 이 축이 배선돼야 R08(STALE)이 실제로 돕니다.
            {datasets.some((d) => d.contract && d.id === 'etf_holdings') && (
              <>
                {' '}
                그중 KRX holdings 는 원천이 기준일을 주지 않아 <b>영구 검증 불가</b>이며, UNKNOWN
                유지가 설계 결정입니다(ADR-0043).
              </>
            )}
          </p>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">수집 → 정제 → 적재</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            데이터셋별 배치 흐름 — 여기가 진짜 배치입니다
          </span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>데이터셋</th>
              <th>작업</th>
              <th>결과</th>
            </tr>
          </thead>
          <tbody>
            {[...byDataset.entries()].map(([d, xs]) => (
              <tr key={d}>
                <td className="mono">{d}</td>
                <td className="col-muted t-xs">{xs.map((x) => x.task_key).join(' → ')}</td>
                <td>
                  {xs.every((x) => x.task_outcome === 'FULFILLED') ? (
                    <StatusBadge tone="active">전건 귀결</StatusBadge>
                  ) : (
                    <StatusBadge tone="blocked">미귀결 포함</StatusBadge>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* 뉴스 데이터셋의 당일 단계별 처리량 — 추이 화면에서 옮겨 왔다(하루의 단면이지 추이가 아니다).
          🔴 이 축만 응답 밖이라 위 표와 **날짜가 다르다** — 그 사실은 카드가 스스로 밝힌다. */}
      <NewsFunnel />
    </div>
  );
}
