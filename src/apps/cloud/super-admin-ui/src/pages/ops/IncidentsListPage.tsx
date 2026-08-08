/* 문제·사건 — 전체 사건 목록 (ALPHA-738).
 *
 * 오늘 페이지에서 떼어낸 화면이다. `/` 는 "지금 무슨 상황인가"의 요약이고, 여기는
 * "걸린 사건 전부를 훑는다" — 두 역할이 한 화면에 있으면 요약이 목록에 묻힌다.
 *
 * 심각도는 **필터**다. 세 목록을 동시에 펼치면 이 화면도 다시 긴 나열이 된다 —
 * 한 번에 하나만 보고, 무엇을 보고 있는지는 URL(?severity=)이 말한다.
 *
 * 판정·정렬은 여기서 아무것도 새로 만들지 않는다. evaluate() 가 낸 사건과 그 순서 그대로다.
 */
import { useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { StatusBadge } from 'ui-kit';
import type { RuleResult, Severity } from '../../rules/types';
import { RULES } from '../../rules/rules';
import {
  Absent,
  Info,
  SEV_TONE,
  SourceChip,
  fmt,
  useConsoleEvaluation,
  violationTip,
} from './shared';
import { incidentHref } from './investigation';
import '../../styles/ops.css';

const SEVERITIES: Severity[] = ['P0', 'P1', 'P2'];
const SEV_MEANING: Record<Severity, string> = {
  P0: '지금 개입이 필요하다',
  P1: '오늘 안에 확인한다',
  P2: '기록해 두고 본다',
};
const isSeverity = (v: string | null): v is Severity => v === 'P0' || v === 'P1' || v === 'P2';

const SCOPE_TIP = [
  '집계 범위 — 실행·작업 실패, 데이터 결손·신선도, 장중 수집 지연·무증거, 분석 생성 실패,',
  '원장·관측 불일치. 테넌트 전달·발번은 이 콘솔 소관이 아니라 개수에서 뺀다(룰은 그대로 돈다).',
  '',
  '정렬 — evaluate() 가 낸 순서 그대로다(심각도 → 연쇄 크기 → 대표 수치).',
  '단위가 다른 수치를 가로질러 비교하지 않으므로 같은 심각도 안의 위아래를 조치 우선순위로',
  '읽지 마라.',
].join('\n');

export function IncidentsListPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const requested = params.get('severity');
  /* URL 이 선택 상태의 정본이다 — 새로고침·링크 공유가 같은 심각도를 연다. 기본은 P0. */
  const selected: Severity = isSeverity(requested) ? requested : 'P0';
  const { pipeline, outOfScope, rules, minuteLoaded } = useConsoleEvaluation();
  const list = pipeline.filter((i) => i.sev === selected);

  /* 화면만 P0 로 떨어뜨리면 주소창이 거짓말을 한다(?severity=foo 인데 P0 를 보여줌).
   * 링크를 공유했을 때도 같은 상태가 열리도록 URL 을 정규화한다. */
  useEffect(() => {
    if (isSeverity(requested)) return;
    const next = new URLSearchParams(params);
    next.set('severity', 'P0');
    setParams(next, { replace: true });
  }, [requested, params, setParams]);

  const select = (sev: Severity) => {
    const next = new URLSearchParams(params);
    next.set('severity', sev);
    /* 필터 전환이지 페이지 이동이 아니라 히스토리를 쌓지 않는다 */
    setParams(next, { replace: true });
  };

  return (
    <div className="flex flex-col gap-4">
      {/* 한 줄로 줄인다 — 설명 문단이 길면 정작 봐야 할 사건 카드를 아래로 민다.
          범위·정렬 규칙처럼 한 번 읽으면 되는 것은 ⓘ 로 내린다. */}
      <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
        파이프라인 소관 문제 {pipeline.length}건
        {outOfScope.length > 0 && ` · 담당 범위 밖 ${outOfScope.length}건 제외`}
        <Info tip={SCOPE_TIP} label="집계 범위와 정렬" />
      </p>

      {/* 선택 컨트롤 — 진짜 버튼이라 Tab·Enter·Space 가 동작하고, 색 외에 표식·aria-pressed 로도 선택을 말한다 */}
      <div className="ops-sev-row" role="group" aria-label="심각도 선택">
        {SEVERITIES.map((sev) => {
          const on = sev === selected;
          const n = pipeline.filter((i) => i.sev === sev).length;
          return (
            <button
              key={sev}
              type="button"
              className={'kpi ops-sev' + (on ? ' ops-sev-on' : '')}
              aria-pressed={on}
              onClick={() => select(sev)}
            >
              <span className="kpi-label">
                <span aria-hidden="true" className="ops-sev-mark">
                  {on ? '▶' : ' '}
                </span>
                <StatusBadge tone={SEV_TONE[sev]}>{sev}</StatusBadge>
                {on && <span className="chip chip-accent">보는 중</span>}
              </span>
              <span className="kpi-value">{n}건</span>
              <span className="kpi-sub">{SEV_MEANING[sev]}</span>
            </button>
          );
        })}
      </div>

      <div className="card">
        {/* 심각도·건수·뜻은 바로 위 선택 카드가 이미 말한다 — 여기서 반복하지 않는다 */}
        <div className="card-head">
          <span className="t-label">사건</span>
        </div>
        {list.length === 0 ? (
          <div className="card-pad">
            <p className="t-sm m-0">{selected} 로 걸린 사건이 없습니다.</p>
            <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
              규칙이 이 심각도로 판정한 것이 오늘 없다는 뜻입니다 — 다른 심각도를 골라 보세요.
            </p>
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="table ops-inc-table">
              <thead>
                <tr>
                  <th>사건</th>
                  <th className="num">수치</th>
                </tr>
              </thead>
              <tbody>
                {list.map((I) => {
                  /* 수치 칸에는 **양만** 선다. 판정 어휘(TIMED_OUT·STALE·미귀결)는 룰이
                   * `state` 로 따로 내므로 여기서 `typeof` 로 갈라 추측하지 않는다 —
                   * 그건 대상 아래 배지 자리다. 우측 정렬 숫자 열에 섞으면 훑을 수 없다. */
                  return (
                    /* 행 전체가 과녁이다. 안쪽 버튼은 키보드 도달용으로 남긴다 —
                       tr 은 Tab 으로 닿지 않는다. */
                    <tr
                      key={I.root.vid}
                      className="ops-run-row"
                      onClick={() => navigate(incidentHref(I.root))}
                    >
                      {/* 대상 + 규칙. 예전에는 룰이 쓴 `title` 을 그대로 냈는데, 어떤 행은
                          대상(LOAD_ETF_HOLDINGS)이고 어떤 행은 증상(두 표면이 다름)이라
                          세로로 훑어지지 않았다. 위반이 이미 들고 있는 축으로 통일한다.
                          층 배지가 앞에 서는 이유: 대상이 룰마다 다른 축이라(런 ID·작업 키·
                          데이터셋 id) 그 사실을 밝히지 않으면 뒤섞인 것처럼 보인다. */}
                      <td>
                        <div className="ops-inc-target">
                          <span className="chip">{I.root.layer}</span>
                          {/* 대상 자체가 이동 버튼이다 — tr 은 Tab 이 닿지 않아 키보드 경로가
                              사라진다(예전엔 `상세 →` 링크가 그 역할이었다) */}
                          <button
                            type="button"
                            className="ops-inc-btn mono"
                            onClick={(e) => {
                              e.stopPropagation();
                              navigate(incidentHref(I.root));
                            }}
                          >
                            {I.root.target}
                          </button>
                        </div>
                        <div className="ops-inc-meta t-xs">
                          <span>{I.root.ruleName}</span>
                          <span className="mono">{I.root.rule}</span>
                          {/* 판정은 수치 칸이 아니라 여기 선다 */}
                          {I.root.state && <span className="chip">{I.root.state}</span>}
                          {/* 흡수된 위반이 있을 때만 — `—` 를 열로 세우면 12행이 소음이다 */}
                          {I.members.length > 0 && (
                            <span className="chip chip-warn">연쇄 {I.members.length}건</span>
                          )}
                          {I.root.mock && <span className="chip">MOCK</span>}
                          {I.root.seed && <span className="chip">SEED</span>}
                          <Info tip={violationTip(I.root, I)} label={`${I.root.ruleName} 판정 근거`} />
                        </div>
                      </td>
                      <td className="num">
                        {I.root.metric != null ? (
                          <>
                            <span>{fmt(I.root.metric)}</span>{' '}
                            <span className="ops-inc-unit t-xs" title={I.root.unit}>
                              {I.root.unit}
                            </span>
                          </>
                        ) : (
                          /* 세는 값이 없다 — 판정은 왼쪽 배지가 이미 말했다 */
                          <Absent kind="none" />
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <RuleCatalog results={rules} minuteLoaded={minuteLoaded} />

    </div>
  );
}

/* ══ 규칙 목록 ══
 *
 * 이 화면의 사건이 어디서 나왔는지 볼 곳이 없었다. 규칙은 `RULES` 배열 하나가 정본이므로
 * 여기서 그대로 편다 — 화면에 규칙을 다시 적지 않는다(적으면 둘이 어긋난다).
 *
 * ⚠️ **평가됨·위반 0** 과 **못 돎** 을 같은 칸에 그리지 않는다. 조용한 규칙은 "괜찮다"이고
 * 못 돈 규칙은 "모른다"다 — 둘을 합치면 계측 공백이 정상으로 보인다.
 */
const RULE_TIP = [
  '이 콘솔의 사건은 전부 아래 규칙이 낸 것이다. 심각도(P0·P1·P2)는 규칙마다 선언된 값이고',
  '개수로 계산하지 않는다 — 같은 심각도 안의 순서는 연쇄 크기·수치일 뿐 조치 우선순위가 아니다.',
  '',
  '평가됨 · 위반 0 — 규칙이 돌았고 조건에 걸린 것이 없다.',
  '못 돎 — 그 규칙이 읽을 사실 축이 아예 없다. "괜찮다"가 아니라 "모른다"다.',
  '응답 결함 — 축은 있었는데 응답이 사건을 못 가르게 줬다(식별자 충돌·빈 키). 계측 공백이',
  '  아니라 계약 위반이고, 그 규칙의 위반은 하나도 안 실린다 — 사유는 그 줄이 그대로 말한다.',
  '',
  '실시간(R17~R19)은 세션 원장이 스냅샷이 아니라 API 응답이라, 그 응답이 오기 전에는 못 돈다.',
].join('\n');

function RuleCatalog({ results, minuteLoaded }: { results: RuleResult[]; minuteLoaded: boolean }) {
  const meta = new Map(RULES.map((R) => [R.id, R]));
  /* 두 종류를 한 숫자로 합치지 않는다 — 응답 결함(계약 위반)이 평상시에도 0 이 아닌
     `못 돎`(계측 공백) 카운트에 섞이면 증분을 아무도 못 본다. */
  const notRun = results.filter((r) => r.notRun === 'axis').length;
  const badResponse = results.filter((r) => r.notRun === 'identity');
  /* 19행을 펼쳐 두면 정작 봐야 할 사건 카드가 화면 밖으로 밀린다 — 참조용이라 기본은 접는다.
     native <details> 를 쓴다: 열림 상태 관리도, 접근성 배선도 브라우저가 한다. */
  return (
    <details className="card ops-rulecat">
      <summary className="card-head">
        <span className="t-label">규칙 {results.length}개</span>
        {notRun > 0 && <StatusBadge tone="neutral">못 돎 {notRun}</StatusBadge>}
        {badResponse.length > 0 && (
          <StatusBadge tone="blocked">응답 결함 {badResponse.length}</StatusBadge>
        )}
        <span className="t-xs" style={{ color: 'var(--fg-3)', marginLeft: 'auto' }}>
          실시간 축 {minuteLoaded ? '실림' : '없음'}
        </span>
      </summary>
      <div className="card-pad" style={{ paddingBottom: 0 }}>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
          이 화면의 사건은 전부 아래 규칙이 낸 것입니다
          <Info tip={RULE_TIP} label="규칙과 심각도" />
        </p>
      </div>
      {/* 넓은 표는 자기 안에서 가로 스크롤한다 — 본문이 가로로 밀리지 않게(위 사건 표와 같은 관용구) */}
      <div style={{ overflowX: 'auto' }}>
        <table className="table">
          <thead>
            <tr>
              <th>규칙</th>
              <th>층</th>
              <th>심각도</th>
              <th>조건</th>
              <th>출처</th>
              <th>상태</th>
            </tr>
          </thead>
          <tbody>
            {results.map((r) => {
              const R = meta.get(r.id);
              return (
                <tr key={r.id}>
                  <td>
                    <span className="mono">{r.id}</span>{' '}
                    <span style={{ fontWeight: 600 }}>{r.name}</span>
                  </td>
                  <td className="col-muted">{r.layer}</td>
                  <td>{R && <StatusBadge tone={SEV_TONE[R.base]}>{R.base}</StatusBadge>}</td>
                  <td className="col-muted t-xs" title={R?.desc}>{R?.desc}</td>
                  <td>{R && <SourceChip source={R.source} />}</td>
                  {/* 위반 수를 따로 세우지 않는다 — 0 의 뜻("돌았고 걸린 게 없다")은 숫자가
                      아니라 문장이어야 하고, 옆 칸의 `못 돎`(모른다)과 갈라 읽혀야 한다.
                      note 는 title 로 — 두 줄이 되면 19행의 높이가 들쭉날쭉해진다.
                      ⚠️ **응답 결함만은 본문으로 낸다.** 사유가 호버에만 있으면 이 표가 기본
                      접힘이라 아무도 못 보고, 그 자리에 `R.dep` 를 그리면 "사실 축 부재"라는
                      **거짓**이 선다 — 축은 멀쩡했고 응답이 사건을 못 가르게 준 것이다. */}
                  <td className="col-muted t-xs" title={r.note ?? undefined}>
                    {r.evaluated ? (
                      r.violations > 0 ? (
                        `위반 ${r.violations}건`
                      ) : (
                        '조건에 걸린 것 없음'
                      )
                    ) : r.notRun === 'identity' ? (
                      <span style={{ color: 'var(--down)' }}>응답 결함 — {r.note}</span>
                    ) : (
                      `못 돎 — ${R?.dep ?? '사실 축 부재'}`
                    )}
                    {r.evaluated && r.note && ' *'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </details>
  );
}
