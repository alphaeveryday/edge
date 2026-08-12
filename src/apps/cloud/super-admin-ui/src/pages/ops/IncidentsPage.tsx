/* 오늘 파이프라인 — 콘솔 홈 (ALPHA-738).
 *
 * 답하는 질문: **오늘 데이터 파이프라인과 분석 결과 생성이 정상으로 수행되고 있는가?**
 *
 * 범위는 파이프라인 실행 · 일배치/실시간 수집 · 데이터 완전성 · 분석 실행 · 분석 결과
 * 생성까지다. 테넌트별 전달·발번·온프렘 수신·최종 게시·MTS 노출은 **이 화면 밖**이다
 * (ADR-0026 · 전달 경계는 /ops/delivery 소관) — 파이프라인 성공을 전달·노출 성공으로
 * 표현하지 않기 위해서다.
 *
 * 이 화면은 "지금 무슨 상황이고 당장 뭘 봐야 하나"까지만 답한다. 걸린 사건 전부를 훑는 일은
 * 문제·사건(/ops/incidents) 소관이다 — 두 역할이 한 화면에 있으면 요약이 목록에 묻힌다.
 *
 * 순서: 기준 시각 → 실시간 수집 → 운영 상태 → 즉시 조치 필요(P0) → 심각도 요약 → 탐지 상태.
 *
 * 판정은 아무것도 새로 만들지 않는다. 운영 상태의 정상/저하/차단은 응답이 준 두 수치의
 * 직접 비교이고, 사건은 evaluate() 결과를 도메인으로 거른 것뿐이다.
 */
import { Link, useNavigate } from 'react-router-dom';
import { StatusBadge } from 'ui-kit';
import type { BadgeTone } from 'ui-kit';
import type { Facts, Incident } from '../../rules/types';
import { useMinuteStatus, useSourceOverview } from '../../domains/sources/hooks';
import { datasetKind, sessionHealth } from '../../domains/sources/minuteView';
import { MOCK_MINUTE } from '../../mock/preview';
import { AwsObservedAt, ConsoleGate, Info, useConsoleEvaluation } from './shared';
import { axisOf } from './consoleFacts';
import { FETCH_LABEL, isCurrent, unevaluatedRules, unreadNote } from './notRun';
import type { AxisFetch } from './notRun';
import { incidentHref } from './investigation';
import { evaluateMetric } from './trendMetrics';
import { buildMetrics } from './trendCatalog';
import { hasNoSignal, hasPendingJobs, healthyClaimed } from '../../domains/sources/minuteView';
import '../../styles/ops.css';

const SCOPE_TIP = [
  '이 화면은 파이프라인 실행 · 일배치/실시간 수집 · 데이터 완전성 · 분석 실행 · 분석 결과',
  '생성 소관의 사건만 센다.',
  '',
  '테넌트별 전달 · tenant_delivery 발번 · 온프렘 consumer 수신 · 최종 게시 · MTS/HTS 노출은',
  '파이프라인 운영 상태가 아니다 — 그 사건은 전달 화면(/ops/delivery)이 답한다.',
  '',
  '규칙은 그대로 다 돌린다. 여기서 거르는 것은 표시일 뿐이라 "안 봤다"와 "봤는데 소관이',
  '아니다"가 뒤섞이지 않는다 — 제외한 건수는 탐지 상태에 그대로 적는다.',
].join('\n');


/* ══ 0. 실시간 수집 — 데이터셋별 지름길 ══
 *
 * 오늘 화면은 **어느 실시간 데이터셋을 지금 봐야 하는가**까지만 답한다. 분 단위 근거·창
 * 구성·긴 설명은 세션 상세(/minute)의 몫이라 여기 펼치지 않는다.
 *
 * 데이터셋마다 따로 판정한다 — 실시간 전체를 하나의 정상/장애로 합치지 않는다(장중은 실행
 * 시간대이지 판정 단위가 아니다). 상태 배지는 서버 판정(phase + lease)의 라벨이고, 옆에
 * 붙는 한 수치는 그 데이터셋의 어휘로만 쓴다.
 */

/** 표시 이름 — 어휘 정본은 data_pipeline/minute/states.py 다. 모르는 값은 원문 그대로 */
const DATASET_LABEL: Record<string, string> = {
  price_minute: '1분 가격',
  news_minute: '뉴스',
};

const REALTIME_TIP = [
  '실시간 수집은 데이터셋마다 따로 판정한다 — 장중은 실행 시간대이지 판정 단위가 아니다.',
  '',
  '한 상태로 합치지 않고 네 축을 따로 잰다.',
  '  생존 — 세션 lease·heartbeat(서버 DB 시계 판정)',
  '  진행 — 연속 완결 워터마크와 마지막 기록',
  '  커버리지 — **기한이 도래한 창** 중 결과 증거가 남은 창',
  '  품질 — 불완전·무효·MISSING',
  '후속 처리 job 은 세션과 입도가 다르며, 그 축을 제공하는 가격·뉴스에서만 별도로 본다.',
  '',
  '전체 상태는 가장 나쁜 축을 따르되 그 이유를 옆에 적는다.',
  '  수집기가 정상이어도 품질 결함이 있으면 주의',
  '  heartbeat 끊김·기한 경과 무증거는 장애',
  '  세션 시작 전은 대기, 종료 국면은 종료(최종 완결 시각과 함께)',
  '',
  '⚠️ 커버리지 분모에 거래일 전체 기대 창(390)을 쓰지 않는다 — 아직 오지 않은 창이',
  '결손처럼 보인다. 응답이 도래 여부를 직접 주지 않아 증거+무증거를 하한으로 쓴다.',
].join('\n');

function RealtimeShortcut({ date }: { date: string }) {
  const { data, isPending, isError } = useMinuteStatus(date);
  /* 세션이 없으면 목으로 미리보기 — 실측이 없다는 사실을 먼저 말하고 나서다 */
  const real = !isPending && !isError && !hasNoSignal(data);
  const view = real ? data : MOCK_MINUTE;
  const newsJobsVisible = Object.values(view.newsJobs).some((count) => count > 0);
  const newsJobsDefect = view.newsJobs.claimedExpired > 0 || view.newsJobs.dead > 0;
  const newsJobsRunning = hasPendingJobs(view.newsJobs);

  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">실시간 수집</span>
        {!real && !isPending && <span className="chip">MOCK</span>}
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          생존 · 진행 · 커버리지 · 품질을 따로 봅니다
          <Info tip={REALTIME_TIP} label="실시간 판정 기준" />
        </span>
        <span className="t-xs" style={{ marginLeft: 'auto' }}>
          <Link to="/minute">현재 실행 전체 →</Link>
        </span>
      </div>
      <div className="card-pad">
        {isPending ? (
          <p className="t-sm m-0" style={{ color: 'var(--fg-3)' }}>
            불러오는 중…
          </p>
        ) : (
          <>
            {!real && (
              <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginBottom: 8 }}>
                {isError
                  ? '실시간 상태를 불러오지 못했습니다 — 아래는 목데이터입니다.'
                  : '오늘 실시간 세션이 없습니다(비거래일 · 미가동) — 아래는 목데이터입니다.'}
              </p>
            )}
            <ul className="ops-rt-list">
              {newsJobsVisible && (
                <li className="ops-rt">
                  <Link to={`/minute?date=${view.date}&dataset=news_minute`} className="ops-rt-link ops-rt-axes">
                    <span className="ops-rt-head">
                      <span className="t-label">뉴스 후속 처리 job</span>
                      <StatusBadge tone={newsJobsDefect ? 'blocked' : 'active'}>
                        {newsJobsDefect ? '확인 필요' : newsJobsRunning ? '처리 중' : '완료'}
                      </StatusBadge>
                      <span className="t-xs ops-lane-link">상세 →</span>
                    </span>
                    <span className="t-xs ops-rt-axis">
                      대기 {view.newsJobs.waiting} · 유효 처리 중 {healthyClaimed(view.newsJobs)} · 고착 {view.newsJobs.claimedExpired} · DEAD {view.newsJobs.dead}
                    </span>
                  </Link>
                </li>
              )}
              {view.sessions.map((s) => {
                const kind = datasetKind(s.dataset);
                const h = sessionHealth(s, s.priceJobs, kind === 'price');
                return (
                  <li key={s.sessionId} className="ops-rt">
                    <Link
                      to={`/minute?date=${view.date}&dataset=${s.dataset}`}
                      className="ops-rt-link ops-rt-axes"
                    >
                      <span className="ops-rt-head">
                        <span className="t-label">{DATASET_LABEL[s.dataset] ?? s.dataset}</span>
                        <StatusBadge tone={h.tone}>{h.label}</StatusBadge>
                        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
                          {h.reason}
                        </span>
                        <span className="t-xs ops-lane-link">상세 →</span>
                      </span>
                      {/* 네 축을 따로 낸다 — "실행 정상" 하나로 합치면 결함이 초록에 묻힌다 */}
                      <span className="t-xs ops-rt-axis" style={{ color: 'var(--fg-2)' }}>
                        {h.liveness}
                      </span>
                      <span className="t-xs ops-rt-axis" style={{ color: 'var(--fg-2)' }}>
                        {h.coverage.text} · {h.quality.text}
                      </span>
                      <span className="t-xs ops-rt-axis" style={{ color: 'var(--fg-3)' }}>
                        {h.progress}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}

/* ══ 1. 현재 상태 — 요약 한 줄 ══
 *
 * 개요는 **상세를 복제하지 않는다.** 각 항목은 숫자 하나와 "어디로 갈지"만 말하고, 판단에
 * 필요한 사실은 그 화면이 답한다. 설명 생성 퍼널·긴 사건 생명주기는 여기 두지 않는다.
 *
 * 전달 경계(Cloud 게시·발번·소비자 수신)는 이 콘솔의 담당 범위 밖이라 항목으로 세우지 않는다.
 */
interface NowItem {
  label: string;
  value: string;
  tone: BadgeTone;
  note: string;
  href: string;
  cta: string;
}

/**
 * 현재 상태 줄.
 *
 * 🔴 **이번에 안 읽힌 축에는 초록을 쓰지 않는다.** 초록(`active`)은 "봤고 괜찮다"라는 가장 강한
 * 주장인데, 상단이 "지금 상태를 모른다"고 경고하는 화면에서 결론이 초록이면 둘 중 하나는
 * 거짓이다. 값을 지우지는 않는다(직전 관측은 아는 것이다): **톤만 내리고 그 사실을 적는다.**
 */
function nowItems(
  sessions: number,
  batchRunning: number,
  dataDefects: number,
  pipeline: Incident[],
  unevaluated: number,
  fetches: { facts: AxisFetch; minute: AxisFetch; overview: AxisFetch },
): NowItem[] {
  /**
   * 🔴 **카드마다 딛는 축이 다르다.** 불리언 하나로 네 카드를 처리하면 두 방향으로 틀린다:
   * 실시간만 안 읽혔는데 개요 기반 카드까지 "직전 응답"이 되고, 반대로 개요만 안 읽혔는데
   * 사실 기반 카드가 아무 표기 없이 초록으로 남는다.
   *
   * ⚠️ 판별자는 `stale` 이 아니라 **`loaded` 인가**다 — `pending`·`error` 는 값이 아예 없어
   * 호출부가 `?? 0` 으로 접는데, 그 0 이 "오늘 세션 없음"이라는 **현재형 단정**으로 그려진다.
   * ⚠️ 막는 것은 "지금 그렇다"를 주장하는 톤(`active`·`env`)뿐이다 — 경고·차단은 안 읽혔어도
   * 여전히 봐야 할 것이라 톤을 내리면 묻힌다.
   */
  const card = (fetch: AxisFetch, tone: NowItem['tone'], note: string) => {
    if (isCurrent(fetch)) return { tone, note };
    const claimsNow = tone === 'active' || tone === 'env';
    return { tone: claimsNow ? ('neutral' as const) : tone, note: `${FETCH_LABEL[fetch]} · ${note}` };
  };
  /* 두 축 위에 선 카드는 **덜 신선한 쪽**을 따른다 — 하나라도 안 읽혔으면 현재형이 아니다 */
  const worse = (a: AxisFetch, b: AxisFetch): AxisFetch => (isCurrent(a) ? b : a);
  const p0 = pipeline.filter((i) => i.sev === 'P0').length;
  const p1 = pipeline.filter((i) => i.sev === 'P1').length;
  return [
    {
      label: '즉시 확인할 문제',
      value: `P0 ${p0} · P1 ${p1}`,
      /* 못 돈 규칙이 있으면 **초록을 쓰지 않는다** — 0 은 "괜찮다"가 아니라 "덜 봤다"다.
       * 이 카드는 사실·실시간 **둘 다** 위에 선다(R17~R19 가 실시간을 읽는다). */
      ...card(
        worse(fetches.facts, fetches.minute),
        p0 > 0 ? 'blocked' : p1 > 0 ? 'warn' : unevaluated > 0 ? 'neutral' : 'active',
        `파이프라인 문제 ${pipeline.length}건` +
          (unevaluated > 0 ? ` · 규칙 ${unevaluated}개는 판정 못 함` : ''),
      ),
      href: '/ops/incidents',
      cta: '문제',
    },
    {
      label: '실행 중인 배치',
      value: `${batchRunning}건`,
      ...card(
        fetches.overview,
        batchRunning > 0 ? 'env' : 'neutral',
        batchRunning > 0 ? '레인 원장 기준 진행 중' : '지금 도는 배치 없음',
      ),
      href: '/minute',
      cta: '현재 실행',
    },
    {
      label: '장중 수집 세션',
      value: `${sessions}개`,
      ...card(
        fetches.minute,
        sessions > 0 ? 'active' : 'neutral',
        sessions > 0 ? '데이터셋별 상태는 아래에서' : '오늘 세션 없음',
      ),
      href: '/minute',
      cta: '현재 실행',
    },
    {
      label: '데이터 결손·지연',
      value: `${dataDefects}건`,
      ...card(fetches.facts, dataDefects > 0 ? 'warn' : 'active', '완전성·기준일 지연 지표 기준'),
      href: '/ops/trend',
      cta: '추이',
    },
  ];
}

function NowStrip({
  facts,
  fetches,
  sessions,
  batchRunning,
  pipeline,
  unevaluated,
}: {
  facts: Facts;
  /* 🔴 이 줄의 수치는 **이번 조회의 것이 아닐 수 있다**(캐시이거나 아예 없다). 축을 안 받으면
   * 상단은 "지금 상태를 모른다"고 경고하는데 여기 `0건` 은 초록으로 서서 한 화면이 반대말을
   * 한다. 카드마다 축이 달라 불리언 하나로는 못 접는다. */
  fetches: { facts: AxisFetch; minute: AxisFetch; overview: AxisFetch };
  sessions: number;
  batchRunning: number;
  pipeline: Incident[];
  unevaluated: number;
}) {
  /* 데이터 결손·지연은 추이 지표의 판정을 그대로 센다 — 개요가 새 판정을 만들지 않는다.
   * 지표는 이제 응답에서 만들어지므로 **같은 사실**을 넘겨야 한다(따로 읽으면 두 화면이
   * 다른 시각의 사실로 다른 수를 말한다). */
  const dataDefects = buildMetrics(facts).filter(
    (m) => m.group === 'batch' && evaluateMetric(m).kind === 'abnormal',
  ).length;
  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">현재 상태</span>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          숫자와 다음 행동만 — 상세는 각 화면이 답합니다
          <Info tip={SCOPE_TIP} label="개요 범위" />
        </span>
      </div>
      <div className="card-pad">
        <ul className="ops-now">
          {nowItems(sessions, batchRunning, dataDefects, pipeline, unevaluated, fetches).map((i) => (
            <li key={i.label}>
              <Link to={i.href} className="ops-now-item">
                <span className="t-label">{i.label}</span>
                <StatusBadge tone={i.tone}>{i.value}</StatusBadge>
                <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
                  {i.note}
                </span>
                <span className="t-xs ops-lane-link">{i.cta} →</span>
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/* ══ 2. 즉시 조치 — 한 줄 목록 ══
 *
 * 개요는 문제 상세를 복제하지 않는다. 조치 명령어·런북·연쇄 위반·근거 전문은 문제와 문제
 * 상세가 답한다 — 여기서는 **무엇이 걸렸는지와 어디로 갈지**까지다.
 */
const TOP_N = 3;

/**
 * P0 0건의 뜻은 **조회 상태에 달려 있다.**
 *
 * 🔴 `stale`(직전 응답은 있으나 마지막 조회 실패)에서 "즉시 개입이 필요한 문제가 없습니다"를
 * 그대로 쓰면, 화면 맨 위의 조회 실패 경고와 **핵심 결론이 정반대 말을 한다**. 없다고 말할 수
 * 있는 것은 이번에 실제로 물어봤을 때뿐이다.
 */
function noP0Sentence(unevaluated: number, stale: boolean): string {
  /* ⚠️ 인자는 **P0 를 만든 축(사실·실시간)의 `stale`** 이지 화면 전체의 낡음이 아니다.
   * 개요(`/sources/overview`)는 이 목록의 입력이 아니라, 그게 안 읽혔다고 "P0 판정이 낡았다"고
   * 말하면 거짓이다(봇이 잡았다).
   *
   * `pending`·`error` 를 따로 안 가르는 이유: 그때는 그 축의 데이터가 **아예 없어** 규칙이
   * `못 돎` 으로 서고, 이미 `unevaluated` 에 세어져 아래 문장이 그 사실을 말한다. 오직
   * `stale` 만 — 캐시가 남아 규칙이 **낡은 사실 위에서 평가되어** `unevaluated` 에 안 잡힌다. */
  if (stale) {
    return unevaluated === 0
      ? '직전 응답에는 P0 가 0건이었습니다 — 마지막 조회가 실패해 지금도 그런지는 알 수 없습니다.'
      : `직전 응답의 P0 는 0건이었고, 그때도 규칙 ${unevaluated}개는 판정을 못 했습니다 — 마지막 조회가 실패해 지금 상태는 알 수 없습니다.`;
  }
  return unevaluated === 0
    ? '즉시 개입이 필요한 문제가 없습니다 — 규칙 전건이 판정했고 P0 0건입니다.'
    : `판정한 규칙 중 P0 는 0건입니다 — 다만 규칙 ${unevaluated}개는 판정을 못 해 그 P0 는 걸렸는지조차 모릅니다.`;
}

function ImmediateAction({
  list,
  total,
  unevaluated,
  stale,
}: {
  list: Incident[];
  total: number;
  /* 판정을 못 한 규칙 수 — **"P0 0건"을 말하기 전에 물어야 하는 것**이다. 규칙이 못 돌면
   * 그 규칙의 P0 는 이 목록에 애초에 오르지 않으므로, 0을 "지금 개입할 것이 없다"로
   * 그리면 계측 공백·조회 실패가 초록으로 보인다. */
  unevaluated: number;
  /* **P0 를 만든 축**(사실·실시간) 중 하나라도 낡았는가 — "P0 0건"을 현재형으로 말해도 되는가.
   * ⚠️ 화면 전체의 낡음이 아니다: 개요는 이 목록의 입력이 아니라 섞으면 거짓이 선다.
   * ⚠️ 이름을 `fetch` 로 두지 마라: 전역 `fetch` 를 가려 tsc 가 엉뚱한 자리를 가리킨다. */
  stale: boolean;
}) {
  const navigate = useNavigate();
  const top = list.slice(0, TOP_N);
  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">즉시 조치</span>
        {list.length > 0 && <StatusBadge tone="blocked">P0 {list.length}건</StatusBadge>}
        <span className="t-xs" style={{ color: 'var(--fg-3)', marginLeft: 'auto' }}>
          <Link to="/ops/incidents">모든 문제 {total}건 보기 →</Link>
        </span>
      </div>
      <div className="card-pad">
        {list.length === 0 ? (
          <p className="t-sm m-0">{noP0Sentence(unevaluated, stale)}</p>
        ) : (
          <ul className="ops-p0-lines">
            {top.map((I) => (
              <li key={I.root.vid}>
                <button
                  type="button"
                  className="ops-p0-line"
                  onClick={() => navigate(incidentHref(I.root))}
                >
                  <StatusBadge tone="blocked">P0</StatusBadge>
                  <span className="t-sm" style={{ fontWeight: 600 }}>
                    {I.root.title}
                  </span>
                  <span className="mono t-xs" style={{ color: 'var(--fg-3)' }}>
                    {I.root.target}
                  </span>
                  <span className="t-xs ops-lane-link">상세 →</span>
                </button>
              </li>
            ))}
            {list.length > top.length && (
              <li className="t-xs" style={{ color: 'var(--fg-3)' }}>
                나머지 P0 {list.length - top.length}건은 <Link to="/ops/incidents">문제</Link>에서
                확인합니다.
              </li>
            )}
          </ul>
        )}
      </div>
    </div>
  );
}

/* ══ 페이지 ══ */
export function IncidentsPage() {
  /* 실시간 축이 실린 평가 — 사건 목록의 유일한 출처다(shared.useConsoleEvaluation) */
  const ev = useConsoleEvaluation();
  const minute = useMinuteStatus(ev.ready ? ev.facts.meta.today : undefined, ev.ready);
  const overview = useSourceOverview();
  if (!ev.ready) return <ConsoleGate q={ev} />;
  const { pipeline, facts } = ev;
  const p0 = pipeline.filter((i) => i.sev === 'P0');
  const unevaluated = unevaluatedRules(ev);
  /* 🔴 이 화면의 결론은 **세 조회** 위에 서 있다 — 사실(P0·규칙)·실시간(세션)·개요(배치 실행).
   * 두 축만 세면 개요가 낡은 날 `실행 중인 배치` 배지가 현재 사실처럼 남는다. */
  const fetches = {
    facts: ev.fetch,
    minute: ev.axisFetch,
    overview: axisOf(overview.data != null, overview.isError),
  };
  /* 상단 한 줄은 합쳐 말하고(어느 축이 왜 안 읽혔는지 이름으로), 카드는 각자 자기 축을 본다 */
  const unread = unreadNote({ 사실: fetches.facts, 실시간: fetches.minute, 개요: fetches.overview });
  /* 🔴 P0 문장은 **자기 입력만** 본다 — 개요는 이 목록을 만들지 않는다. 그걸 섞으면 개요 조회
   * 하나가 실패한 날 "P0 판정이 낡았다"는 거짓이 서고, 반대로 개요 경고는 상단·NowStrip 이
   * 이미 자기 자리에서 말한다. */
  const p0Stale = fetches.facts === 'stale' || fetches.minute === 'stale';
  const sessions = minute.data?.sessions.length ?? 0;
  const batchRunning =
    overview.data?.lanes.filter(
      (l) => l.orchestrationStatus === 'RUNNING' || l.opsStatus === 'IN_PROGRESS',
    ).length ?? 0;

  return (
    <div className="flex flex-col gap-4">
      {/* 기준 시각만 — 규칙 엔진 설명은 하단 탐지 상태에 있다 */}
      <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
        조회일 {facts.meta.today} · DB {hm(facts.meta.db)} · AWS/S3{' '}
        <AwsObservedAt meta={facts.meta} format={hm} />
        {unread && <b style={{ color: 'var(--warn)' }}> · {unread}</b>}
      </p>

      <NowStrip
        facts={facts}
        fetches={fetches}
        sessions={sessions}
        batchRunning={batchRunning}
        pipeline={pipeline}
        unevaluated={unevaluated.length}
      />
      <ImmediateAction
        list={p0}
        total={pipeline.length}
        unevaluated={unevaluated.length}
        stale={p0Stale}
      />
      <RealtimeShortcut date={facts.meta.today} />
    </div>
  );
}

/** 기준 시각은 시:분까지만 — 개요에서 초 단위는 읽히지 않는다 */
function hm(iso: string): string {
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(iso));
}
