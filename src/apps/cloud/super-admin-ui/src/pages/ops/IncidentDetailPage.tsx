/* 사건 상세 — 조사의 출발점 (ALPHA-738).
 *
 * 문제 카드의 "상세"가 같은 자리에서 긴 글을 펼치고 끝나면 조사가 시작되지 않는다. 이 화면은
 * **다음에 무엇을 열어야 하는가**까지 답하고, 그 다음 화면들은 기존 것을 그대로 쓴다
 * (실행 → /ops/runs · 세션 → /minute · 데이터셋 → /ops/datasets · 원장 근거 → /sources).
 *
 * 라우트라서 뒤로 가기·딥링크·새로고침이 그대로 동작한다.
 *
 * 지키는 선:
 *   · 판정을 새로 만들지 않는다 — 심각도·연쇄·근거는 evaluate() 결과 그대로다.
 *   · 없는 축을 지어내지 않는다. 최초 탐지 시각은 위반 단위 계측이 없어 "계측 없음"이다
 *     (스냅샷은 평가 시점만 안다).
 *   · 실행이 없는 사건은 실행 화면으로 보내지 않는다 — investigate() 가 그렇게 판정한다.
 */
import { Link, useSearchParams } from 'react-router-dom';
import { StatusBadge } from 'ui-kit';
import { RULES } from '../../rules/rules';
import { ruleOfVid } from '../../rules/evaluate';
import { runbookOf } from '../../rules/evaluate';
import type { AxisFetch } from './notRun';
import {
  Absent,
  AwsObservedAt,
  ConsoleGate,
  Info,
  SEV_TONE,
  SourceChip,
  domainOf,
  fmt,
  kst,
  useConsoleEvaluation,
  useFocusRow,
} from './shared';
import { isCurrent, isKnownVid, notRunReason, unevaluatedFor } from './notRun';
import { incidentHref, incidentOfVid, investigate, ledgerHref } from './investigation';
import '../../styles/ops.css';

const TARGET_LABEL: Record<string, string> = {
  run: '실행',
  slot: '예정 슬롯',
  session: '실시간 세션',
  dataset: '데이터셋',
  queue: '큐 · 배포',
  output: '산출 · 흐름',
};

function Fact({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="t-xs ops-fact">
      <span style={{ color: 'var(--fg-3)' }}>{k}</span>
      <span style={{ color: 'var(--fg-1)' }}>{children}</span>
    </div>
  );
}

export function IncidentDetailPage() {
  const vid = useSearchParams()[0].get('vid') ?? '';
  /* `?focus=<vid>` 로 들어오면 아래 연쇄 표의 그 줄을 지목한다(흡수된 위반의 링크가 보내는 곳).
   * 훅이라 **조기 반환보다 앞에서** 부른다. */
  useFocusRow();
  const ev = useConsoleEvaluation();
  if (!ev.ready) return <ConsoleGate q={ev} />;
  const { incidents, facts } = ev;
  /* 🔴 **사건마다 딛는 축이 다르다.** 실시간 규칙(R17~R19)은 `/sources/minute` 를, 나머지는
   * 배치 사실을 읽는다. 두 축을 OR 로 접으면 실시간만 안 읽힌 날 **최신 R04 사건까지** "직전
   * 응답"으로 표시되고, 반대로 한 축만 보면 낡은 실시간 사건이 현재형으로 선다.
   * 그래서 이 vid 를 낼 규칙의 축을 보고 그 축의 조회 상태로 판정한다.
   * ⚠️ 규칙을 못 찾으면(낡은·손으로 친 vid) **덜 신선한 쪽**을 쓴다 — 모를 때 현재형은 위험하다. */
  const axisOfVid = (id: string): AxisFetch => {
    const R = RULES.find((X) => X.id === ruleOfVid(id));
    if (R === undefined) return isCurrent(ev.fetch) ? ev.axisFetch : ev.fetch;
    return R.axis === 'minute' ? ev.axisFetch : ev.fetch;
  };
  const stale = !isCurrent(axisOfVid(vid));
  const found = incidentOfVid(incidents, vid);
  /* 흡수된 위반은 **단독 사건이 아니다** — 여기서 그 vid 로 사건 화면을 만들지 않고 뿌리로 보낸다 */
  const incident = found && !found.member ? found.incident : undefined;

  if (!incident) {
    /* 공유 링크의 도착지다 — 여기서 "해소"라고 단정하면 이 PR 이 없애려던 오독을 이 PR 이 낸다.
     * 사건이 안 보이는 이유는 **넷**이다. 어느 쪽인지 화면이 아는데도 한 문장으로 덮으면,
     * 안 보이는 것 전부가 "괜찮아졌다"로 읽힌다.
     *
     *   ① 그 위반이 살아 있는데 인과 간선으로 **다른 사건에 흡수**됐다 — 뿌리로 보내야 한다.
     *      `incidents[]` 는 뿌리만 담아서 이 조회로는 안 잡힌다. 어제 뿌리였던 vid 가 오늘
     *      부모가 걸리면 멤버로 내려가므로, 어제 공유한 링크가 정확히 이 분기로 온다.
     *   ② 그 규칙이 판정을 못 했다 — 해소가 아니라 **걸렸는지조차 모른다**.
     *   ③ 그런 규칙이 애초에 없다(개명·삭제·손으로 친 주소) — "돌았다"를 연역할 수 없다.
     *   ④ 규칙은 돌았고 안 걸렸다 — 이때만 해소·낡은 링크를 말할 수 있다. */
    const absorbed = found?.member ? found.incident : undefined;
    const notRun = unevaluatedFor(ev, vid);
    return (
      <div className="card card-pad">
        <p className="t-sm m-0">
          {absorbed ? '이 위반은 단독 사건이 아닙니다.' : '이 사건을 찾을 수 없습니다.'}
        </p>
        {absorbed ? (
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
            {/* 🔴 `stale` 에서 "지금도"는 거짓이다 — 직전 응답의 사건을 현재로 확정하는 것이다.
                이 분기는 not-found 갈래보다 **먼저** 돌아서, 아래 stale 가드가 못 막는다.
                ⚠️ 접두사만 고치면 안 된다: 뒷문장("해소된 것이 아니라 … 조치는 그 뿌리 하나")도
                현재 상태와 현재 조치를 단정한다. 마지막 조회 이후 인과가 바뀌었을 수 있다. */}
            <code>{vid}</code> 는 {stale ? '직전 응답에서' : '지금도'} 규칙에 걸려 있습니다 —
            해소된 것이 아니라 인과 간선으로 <b>{absorbed.root.title}</b> 사건에{' '}
            {/* ⚠️ 두 시제를 한 문장에 섞지 않는다 — 낡지 않았으면 그 관계는 **지금** 살아 있다.
                R3 에서 앞뒤를 따로 고치다 "지금도 … 흡수돼 있었습니다"가 됐다. */}
            {stale ? '흡수돼 있었습니다' : '흡수됐습니다'}. 조치는 그 뿌리 하나입니다.
            {stale && (
              <b style={{ color: 'var(--warn)' }}>
                {' '}
                마지막 조회가 실패해 인과와 조치 대상이 지금도 같은지는 알 수 없습니다.
              </b>
            )}{' '}
            {/* 뿌리 화면에서 이 위반 줄을 지목한다 — 뿌리만 열면 원래 주소가 가리키던 사실이
                연쇄 표 어딘가로 사라진다(`useFocusRow` 가 그 줄로 스크롤·강조한다) */}
            <Link to={`${incidentHref(absorbed.root)}&focus=${encodeURIComponent(vid)}`}>
              그 사건에서 이 위반 보기 →
            </Link>
          </p>
        ) : notRun ? (
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
            사건 식별자 <code>{vid}</code> 를 낼 규칙은 <span className="mono">{notRun.id}</span>{' '}
            {notRun.name} 입니다. 이 규칙이 이번 평가에서{' '}
            <b style={{ color: 'var(--down)' }}>판정을 못 했습니다</b> (
            {notRunReason(notRun, ev.axisFetch)}) — 위반이 하나도 실리지 않아, 해소됐는지 아직 걸려
            있는지 여기서는 알 수 없습니다. <Link to="/ops/incidents">문제·사건으로</Link>
          </p>
        ) : !isKnownVid(vid) ? (
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
            사건 식별자 <code>{vid || '(없음)'}</code> 는 이 콘솔의 규칙이 낸 형태가 아닙니다 —
            규칙이 개명·삭제됐거나 주소가 잘못됐습니다. 규칙이 판정한 결과가 아니므로{' '}
            <b>무엇이 있었는지는 여기서 알 수 없습니다</b>.{' '}
            <Link to="/ops/incidents">문제·사건으로</Link>
          </p>
        ) : stale ? (
          /* 🔴 `stale` 에서 "해소"를 말하면 **조회 실패가 해결로 읽힌다.** 판정은 섰지만 그
             근거가 직전 응답이라, 지금도 안 걸리는지는 이 화면이 답할 수 없다. */
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
            사건 식별자 <code>{vid}</code> 가 <b>직전 응답</b>의 평가 결과에 없습니다. 다만 마지막
            조회가 실패해 <b style={{ color: 'var(--warn)' }}>지금도 그런지는 알 수 없습니다</b> —
            해소됐다고 단정하지 않습니다. <Link to="/ops/incidents">문제·사건으로</Link>
          </p>
        ) : (
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
            사건 식별자 <code>{vid}</code> 가 지금 평가 결과에 없습니다 — 규칙은 돌았고 더는 걸리지
            않거나(해소) 링크가 낡았습니다. 다른 사건으로 대체해 보여주지 않습니다.{' '}
            <Link to="/ops/incidents">문제·사건으로</Link>
          </p>
        )}
      </div>
    );
  }

  const v = incident.root;
  const rule = RULES.find((r) => r.id === v.rule);
  /* 평가에 쓴 사실 그대로 — 정적 F 로 만들면 실시간 사건이 없는 날짜의 세션을 가리킨다 */
  const { targets, ledger, ledgerNote } = investigate(incident, facts);
  const ledgerTo = ledgerHref(ledger);
  const rb = runbookOf(facts, v);

  return (
    <div className="flex flex-col gap-4">
      {/* ── breadcrumb — 실제 식별자로만 만든다 ── */}
      <nav className="t-xs ops-crumb" aria-label="조사 경로">
        <Link to="/ops/incidents">문제·사건</Link>
        <span aria-hidden="true">›</span>
        <span style={{ color: 'var(--fg-1)' }}>{v.title}</span>
      </nav>

      {/* ── 1. 사건 ── */}
      <div className="card">
        <div className="card-head">
          <StatusBadge tone={SEV_TONE[incident.sev]}>{incident.sev}</StatusBadge>
          <span className="t-h3">{v.title}</span>
          <span className="chip chip-accent">{domainOf(v)}</span>
          <span className="chip">{v.kls}</span>
          {/* 판정 어휘는 양이 아니다 — 수치 자리가 아니라 배지로 선다(목록과 같은 규약) */}
          {v.state && <span className="chip">{v.state}</span>}
          {v.mock && <span className="chip">MOCK</span>}
          {v.seed && <span className="chip">SEED</span>}
          {rule && <SourceChip source={rule.source} />}
        </div>
        <div className="card-pad">
          <p className="t-sm m-0">
            {/* 세는 값이 없는 위반은 수치를 지어내지 않는다 — 판정은 위 배지가 이미 말했다 */}
            {v.metric != null && (
              <>
                <b>{fmt(v.metric)}</b> <span style={{ color: 'var(--fg-3)' }}>{v.unit}</span> ·{' '}
              </>
            )}
            <span style={{ color: 'var(--fg-2)' }}>{v.why}</span>
          </p>

          <div className="ops-facts" style={{ marginTop: 10 }}>
            <Fact k="대상">
              <span className="mono">{v.target}</span>
            </Fact>
            <Fact k="탐지 규칙">
              <span className="mono">{v.rule}</span> {v.ruleName} · {v.layer} 층
              {rule && <Info tip={`조건: ${rule.desc}\n기본 심각도: ${rule.base}`} label="규칙 조건" />}
            </Fact>
            {/* `stale` 이면 "지금"이 거짓이다 — 판정은 섰지만 그 근거가 직전 응답이다.
                한 문장으로 두면 조회 실패가 현재 사실로 읽힌다. */}
            <Fact k="현재 상태">
              {stale
                ? '직전 응답 시점에 성립 — 마지막 조회가 실패해 지금도 걸려 있는지는 알 수 없다'
                : '평가 시점에 성립 — 이 위반은 지금 규칙에 걸려 있다'}
            </Fact>
            <Fact k="최근 관측">
              DB {kst(facts.meta.db)} · AWS/S3{' '}
              <AwsObservedAt meta={facts.meta} />
            </Fact>
            {/* 위반 단위 first_seen 계측이 없다 — 스냅샷은 평가 시점만 안다 */}
            <Fact k="최초 탐지">
              <Absent kind="uninstrumented" />
              <Info
                tip={'위반 단위의 최초 탐지 시각을 남기는 계측이 없다. 이 화면이 아는 것은 평가에 쓴 사실의 기준 시각뿐이라, "언제부터"를 만들어내지 않는다.'}
                label="최초 탐지"
              />
            </Fact>
            <Fact k="영향 범위">
              {v.list ? (
                `${v.list.length}건 — ${v.list.join(' · ')}`
              ) : v.metric != null ? (
                `${fmt(v.metric)} ${v.unit ?? ''}`
              ) : (
                /* 범위를 세는 계측이 없다 — 대상 하나라고 단정하지 않는다 */
                <Absent kind="none" />
              )}
            </Fact>
            {v.lastok && (
              <Fact k="마지막 정상">
                {kst(v.lastok)} · 귀결률 {v.okrate ?? '—'}
              </Fact>
            )}
          </div>

          <p className="t-xs m-0" style={{ marginTop: 10, color: 'var(--fg-2)' }}>
            <b>판정 근거</b> — {v.evidence}
          </p>
          <p className="t-xs m-0" style={{ marginTop: 6 }}>
            <span className="t-label">조치</span>{' '}
            {rb ? (
              <code className="ops-p0-cmd">{rb.cmd}</code>
            ) : (
              <span style={{ color: 'var(--fg-3)' }}>런북 미등록</span>
            )}
            {rb?.note && <span style={{ color: 'var(--fg-3)' }}> — {rb.note}</span>}
          </p>
        </div>
      </div>

      {/* ── 2. 연쇄 — 흡수된 위반 ── */}
      {incident.members.length > 0 && (
        <div className="card">
          <div className="card-head">
            <span className="t-label">이 사건에 묶인 위반 {incident.members.length}건</span>
            <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
              인과 간선으로 접혔습니다 — 조치는 위 하나입니다
            </span>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>규칙</th>
                <th>위반</th>
                <th>대상</th>
                <th>왜 이 사건의 결과인가</th>
              </tr>
            </thead>
            <tbody>
              {incident.members.map((m) => (
                /* id 는 `useFocusRow` 가 찾는 축이다 — 흡수된 위반의 딥링크가 이 줄로 온다 */
                <tr key={m.v.vid} id={m.v.vid}>
                  <td className="mono">{m.v.rule}</td>
                  <td>{m.v.title}</td>
                  <td className="mono col-muted">{m.v.target}</td>
                  <td className="col-muted t-xs">{m.why}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── 3. 조사 다음 단계 ── */}
      <div className="card">
        <div className="card-head">
          <span className="t-label">조사 다음 단계</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            이 사건이 실제로 들고 있는 식별자로만 엽니다 — 무관한 최근 실행을 붙이지 않습니다
          </span>
        </div>
        <div className="card-pad">
          {targets.length === 0 ? (
            <p className="t-sm m-0" style={{ color: 'var(--fg-3)' }}>
              이 사건에는 연결된 조사 대상이 없습니다 — 실행을 추측해 연결하지 않습니다.
            </p>
          ) : (
            <ul className="ops-target-list">
              {targets.map((t) => (
                <li key={`${t.kind}|${t.id}`}>
                  <Link to={t.href} className="ops-target">
                    <span className="chip chip-accent">{TARGET_LABEL[t.kind] ?? t.kind}</span>
                    <span className="t-sm" style={{ fontWeight: 600 }}>
                      {t.label}
                    </span>
                    <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
                      {t.why}
                    </span>
                    <span className="t-xs ops-lane-link">열기 →</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}

          {/* 원장 근거 — 조사의 마지막 단계다. 문맥이 없으면 링크를 만들지 않는다 */}
          <div className="ops-ledger-cta">
            {ledgerTo ? (
              <>
                <Link to={ledgerTo} className="btn btn-sm">
                  원장 근거 보기 →
                </Link>
                <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
                  이 사건의 문맥({[ledger?.runKey, ledger?.task, ledger?.dataset, ledger?.date]
                    .filter(Boolean)
                    .join(' · ')})으로 범위를 좁혀 원시 사실만 봅니다
                </span>
              </>
            ) : (
              <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
                <b>원장 근거 없음</b> — {ledgerNote}
              </span>
            )}
          </div>
          {ledgerTo && ledgerNote && (
            <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 6 }}>
              {ledgerNote}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
