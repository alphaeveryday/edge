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
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { StatusBadge } from 'ui-kit';
import type { BadgeTone } from 'ui-kit';
import type { Incident } from '../../rules/types';
import { useMinuteStatus, useSourceOverview } from '../../domains/sources/hooks';
import { datasetKind, sessionHealth } from '../../domains/sources/minuteView';
import { MOCK_MINUTE } from '../../mock/preview';
import { F, Info, PIPELINE_INCIDENTS } from './shared';
import { incidentHref } from './investigation';
import { evaluateMetric } from './trendMetrics';
import { METRICS } from './trendCatalog';
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
  '  품질 — 불완전·무효·MISSING·DEAD·유효 lease 없는 claim',
  '',
  '전체 상태는 가장 나쁜 축을 따르되 그 이유를 옆에 적는다.',
  '  수집기가 정상이어도 품질 결함이 있으면 주의',
  '  heartbeat 끊김·기한 경과 무증거는 장애',
  '  세션 시작 전은 대기, 종료 국면은 종료(최종 완결 시각과 함께)',
  '',
  '⚠️ 커버리지 분모에 거래일 전체 기대 창(390)을 쓰지 않는다 — 아직 오지 않은 창이',
  '결손처럼 보인다. 응답이 도래 여부를 직접 주지 않아 증거+무증거를 하한으로 쓴다.',
].join('\n');

function RealtimeShortcut() {
  const { data, isPending, isError } = useMinuteStatus();
  /* 세션이 없으면 목으로 미리보기 — 실측이 없다는 사실을 먼저 말하고 나서다 */
  const real = !isPending && !isError && data.sessions.length > 0;
  const view = real ? data : MOCK_MINUTE;

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
              {view.sessions.map((s) => {
                const kind = datasetKind(s.dataset);
                const h = sessionHealth(s, kind === 'news' ? view.newsJobs : s.priceJobs);
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

function nowItems(sessions: number, batchRunning: number, dataDefects: number): NowItem[] {
  const p0 = PIPELINE_INCIDENTS.filter((i) => i.sev === 'P0').length;
  const p1 = PIPELINE_INCIDENTS.filter((i) => i.sev === 'P1').length;
  return [
    {
      label: '즉시 확인할 문제',
      value: `P0 ${p0} · P1 ${p1}`,
      tone: p0 > 0 ? 'blocked' : p1 > 0 ? 'warn' : 'active',
      note: `파이프라인 문제 ${PIPELINE_INCIDENTS.length}건`,
      href: '/ops/incidents',
      cta: '문제',
    },
    {
      label: '실행 중인 배치',
      value: `${batchRunning}건`,
      tone: batchRunning > 0 ? 'env' : 'neutral',
      note: batchRunning > 0 ? '레인 원장 기준 진행 중' : '지금 도는 배치 없음',
      href: '/minute',
      cta: '현재 실행',
    },
    {
      label: '장중 수집 세션',
      value: `${sessions}개`,
      tone: sessions > 0 ? 'active' : 'neutral',
      note: sessions > 0 ? '데이터셋별 상태는 아래에서' : '오늘 세션 없음',
      href: '/minute',
      cta: '현재 실행',
    },
    {
      label: '데이터 결손·지연',
      value: `${dataDefects}건`,
      tone: dataDefects > 0 ? 'warn' : 'active',
      note: '완전성·기준일 지연 지표 기준',
      href: '/ops/trend',
      cta: '추이',
    },
  ];
}

function NowStrip({ sessions, batchRunning }: { sessions: number; batchRunning: number }) {
  /* 데이터 결손·지연은 추이 지표의 판정을 그대로 센다 — 개요가 새 판정을 만들지 않는다 */
  const dataDefects = METRICS.filter(
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
          {nowItems(sessions, batchRunning, dataDefects).map((i) => (
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

function ImmediateAction({ list }: { list: Incident[] }) {
  const navigate = useNavigate();
  const top = list.slice(0, TOP_N);
  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">즉시 조치</span>
        {list.length > 0 && <StatusBadge tone="blocked">P0 {list.length}건</StatusBadge>}
        <span className="t-xs" style={{ color: 'var(--fg-3)', marginLeft: 'auto' }}>
          <Link to="/ops/incidents">모든 문제 {PIPELINE_INCIDENTS.length}건 보기 →</Link>
        </span>
      </div>
      <div className="card-pad">
        {list.length === 0 ? (
          <p className="t-sm m-0">
            즉시 개입이 필요한 문제가 없습니다 — P0 0건.
          </p>
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
  const [p0] = useState(() => PIPELINE_INCIDENTS.filter((i) => i.sev === 'P0'));
  const minute = useMinuteStatus();
  const overview = useSourceOverview();
  const sessions = minute.data?.sessions.length ?? 0;
  const batchRunning =
    overview.data?.lanes.filter(
      (l) => l.orchestrationStatus === 'RUNNING' || l.opsStatus === 'IN_PROGRESS',
    ).length ?? 0;

  return (
    <div className="flex flex-col gap-4">
      {/* 기준 시각만 — 규칙 엔진 설명은 하단 탐지 상태에 있다 */}
      <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
        거래일 {F.meta.today} · DB {hm(F.meta.db)} · AWS/S3 {hm(F.meta.aws)}
      </p>

      <NowStrip sessions={sessions} batchRunning={batchRunning} />
      <ImmediateAction list={p0} />
      <RealtimeShortcut />
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
