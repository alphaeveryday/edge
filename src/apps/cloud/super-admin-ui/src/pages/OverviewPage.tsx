/* Run Overview — 첫 화면 (ALPHA-683).
 *
 * 중심 질문은 "파이프라인이 돌았는가?"가 아니라 "오늘 어떤 설명을 신뢰하고 발행할 수 있고,
 * 못 하는 것은 왜인가"다. 이 화면은 그 질문의 **파이프라인 원장 쪽 절반**(레인별 최신 런의
 * 운영 상태·필수 작업 귀결·결함 목록)을 답하고, 발행 분포(자동/검수/차단)는
 * 증권사 관리 환경 콘솔 소관이라 여기서 지어내지 않는다.
 *
 * 판정(opsStatus·결함·overdue)은 전부 서버가 한다 — 화면은 라벨만 붙인다(GridPage 와 같은
 * 원칙). 멘토 규칙: 숫자에는 단위를 붙이고, 모든 숫자는 근거 화면으로 내려갈 수 있어야 한다.
 */
import { Link, useNavigate } from 'react-router-dom';
import { PageSkeleton, StatusBadge } from 'ui-kit';
import type { BadgeTone } from 'ui-kit';
import type {
  MinuteSession,
  MinuteStatus,
  OpsStatus,
  OverviewDefect,
  OverviewLane,
  SourceOverview,
} from '../domains/sources';
import { useMinuteStatus, useSourceOverview } from '../domains/sources/hooks';
import { issues, liveness } from '../domains/sources/minuteView';
import { MOCK_MINUTE, MOCK_OVERVIEW } from '../mock/preview';
import { EmptyRealNotice, MockChip, MockPreview } from './_shared/MockPreview';
import { LoadError } from './_shared/LoadError';

/* 스펙 §7 어휘의 표시 라벨. READY 는 "모두 자동 발행"이 아니라 "운영 결함으로 막힌 것이
 * 없다"는 뜻이라 문구를 그렇게 쓴다. */
const OPS: Record<OpsStatus, { label: string; tone: BadgeTone; desc: string }> = {
  READY: { label: '정상', tone: 'active', desc: '운영 결함으로 차단된 대상 없음' },
  /* "마감 전" 을 넣지 않는다 — RUNNING 런은 마감 경과 미귀결이 있어도 IN_PROGRESS 라,
   * 헤더가 "마감 전"이라 말하며 결함 목록이 "마감 경과"를 보이는 모순이 생긴다 */
  IN_PROGRESS: { label: '진행 중', tone: 'env', desc: '필수 작업 판정 대기' },
  /* "아래 목록"이라 쓰지 않는다 — 실행 전체(orchestration) terminal 실패는 작업 결함 목록이
   * 비어도 DEGRADED 라, 목록 없는 카드에서 그 문구가 거짓말이 된다 */
  DEGRADED: { label: '부분 결함', tone: 'warn', desc: '운영 결함 있음 — 결함 목록·실행 축 확인' },
  BLOCKED: { label: '차단', tone: 'blocked', desc: '런이 기동하지 못함 — 전 대상 영향' },
  /* 서버의 UNKNOWN 사유는 실행 불명만이 아니다(계획 증거 없음·마감 없는 미귀결 포함) —
   * "실행 여부"로 좁혀 쓰면 실행 축이 SUCCEEDED 인 카드와 문구가 모순된다 */
  UNKNOWN: { label: '확인 불가', tone: 'neutral', desc: '판정 근거 부족 — 드릴다운 확인 필요' },
};

const LANE_LABEL: Record<string, string> = { 'etf-daily': '시장(EOD)', news: '뉴스' };

/** 결함 사유 — 축 원문을 운영자 어휘로. 여러 축이 겹치면 전부 나열한다(뭉개면 원인이 사라진다). */
function defectReasons(d: OverviewDefect): string[] {
  const reasons: string[] = [];
  if (d.outcome === 'FAILED') reasons.push('실행 실패');
  if (d.outcome === 'MISSED') reasons.push('미실행');
  if (d.outcome === 'BLOCKED') reasons.push('선행 미충족');
  if (d.dataStatus === 'INCOMPLETE') reasons.push('데이터 불완전');
  if (d.dataStatus === 'INVALID') reasons.push('데이터 오류');
  if ((d.failedRecords ?? 0) > 0) reasons.push(`유실 ${d.failedRecords}건`);
  if (d.freshnessStatus === 'STALE') reasons.push('기준일 오래됨');
  if (d.overdue) reasons.push('마감 경과 미귀결');
  return reasons;
}

function LaneCard({ lane, mock = false }: { lane: OverviewLane; mock?: boolean }) {
  const navigate = useNavigate();
  const ops = OPS[lane.opsStatus] ?? OPS.UNKNOWN;
  const c = lane.counts;
  const openDrilldown = (taskKey?: string) =>
    mock
      ? undefined
      : navigate(
      `/sources?runKey=${encodeURIComponent(lane.runKey)}${
        taskKey ? `&task=${encodeURIComponent(taskKey)}` : ''
      }`,
    );

  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">
          {LANE_LABEL[lane.pipelineType] ?? lane.pipelineType} 레인 {mock && <MockChip />}
        </span>
        <StatusBadge tone={ops.tone}>{ops.label}</StatusBadge>
        {/* 오늘 런이 아니면 판정 전체가 지난 런 기준이라는 사실이 상태보다 먼저 보여야 한다 */}
        {lane.notToday && <StatusBadge tone="warn">오늘 런 아님</StatusBadge>}
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>{ops.desc}</span>
      </div>

      {/* 어느 런에 대한 판정인지 — 숫자의 기준(run·기준일)을 화면에 명시한다 */}
      <p className="t-xs m-0" style={{ color: 'var(--fg-2)' }}>
        <button
          type="button"
          className="t-xs"
          onClick={() => openDrilldown()}
          style={{ color: 'var(--fg-2)', textDecoration: 'underline', cursor: 'pointer', background: 'none', border: 0, padding: 0 }}
          title="이 런의 드릴다운으로 이동"
        >
          {lane.runKey}
        </button>
        {' · '}기준 거래일 {lane.tradingDate ?? '—'}
        {/* notToday 가 서버 KST 판정이라 표시도 KST 고정 — 브라우저 시간대로 내면 자정 부근에
         *  "어제 날짜인데 오늘 런" 으로 보이는 모순이 생긴다 */}
        {' · '}계획{' '}
        {lane.plannedAt
          ? `${new Date(lane.plannedAt).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })} KST`
          : '—'}
        {' · '}기동 {lane.launchStatus ?? '—'} · 실행 전체 {lane.orchestrationStatus ?? '—'}
        {/* 뉴스 레인은 문서 계보가 근거 화면이다 — 집계에서 계보로 내려가는 고리(ALPHA-692) */}
        {lane.pipelineType === 'news' && lane.tradingDate && (
          <>
            {' · '}
            <Link to={`/lineage/news?date=${lane.tradingDate}`}>이 거래일 뉴스 계보</Link>
          </>
        )}
      </p>

      {/* 단위를 붙인 카운트 — "필수 작업 N개 중"이 분모다(멘토: 단위 없는 숫자 금지) */}
      <p className="t-sm m-0" style={{ marginTop: 8 }}>
        필수 작업 <b>{c.requiredDue}개</b> 중 완료 <b>{c.fulfilled}</b>
        {c.failed > 0 && <> · 실패 <b style={{ color: 'var(--down, #b91c1c)' }}>{c.failed}</b></>}
        {c.missed > 0 && <> · 미실행 <b style={{ color: 'var(--down, #b91c1c)' }}>{c.missed}</b></>}
        {c.blocked > 0 && <> · 선행 미충족 <b style={{ color: '#b45309' }}>{c.blocked}</b></>}
        {c.pending > 0 && <> · 대기 <b>{c.pending}</b></>}
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          {' '}(실행 대상 {c.due}개{c.skipped > 0 ? ` · 계획 스킵 ${c.skipped}개` : ''})
        </span>
      </p>

      {lane.defects.length > 0 && (
        <div style={{ marginTop: 10 }}>
          {/* "첫 행 = 최초 결함 지점"이라 주장하지 않는다 — 원장은 단계(stage)까지만 순서를
           * 알고, 같은 단계 안의 실행 순서(SFN·카탈로그)는 모른다. 사전순을 실행 순서처럼
           * 내면 하류 증상이 최초 결함으로 오독된다(봇 리뷰 P2). */}
          <p className="t-xs m-0" style={{ fontWeight: 700, color: 'var(--fg-2)' }}>
            결함 {lane.defects.length}건 — 단계(수집→정제→적재) 순, 같은 단계 안 순서는 실행
            순서가 아님. 정확한 지점은 드릴다운에서
          </p>
          <table style={{ borderCollapse: 'collapse', fontSize: 12, marginTop: 4 }}>
            <tbody>
              {lane.defects.map((d) => (
                <tr
                  key={d.taskKey}
                  /* holdings 결손은 영향 화면(ALPHA-686)이 답한다 — 나머지는 드릴다운 */
                  onClick={() =>
                    d.taskKey === 'ETF_HOLDINGS_COLLECTION_KRX'
                      ? navigate(`/impact/holdings?runKey=${encodeURIComponent(lane.runKey)}`)
                      : openDrilldown(d.taskKey)
                  }
                  style={{ cursor: 'pointer' }}
                  title={
                    d.taskKey === 'ETF_HOLDINGS_COLLECTION_KRX'
                      ? '결손 영향 화면으로 이동'
                      : '작업 드릴다운으로 이동'
                  }
                >
                  <td style={{ padding: '2px 10px 2px 0', whiteSpace: 'nowrap' }}>{d.taskKey}</td>
                  <td style={{ padding: '2px 0', color: 'var(--down, #b91c1c)' }}>
                    {defectReasons(d).join(' · ') || '결함'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* 장중 1분 레인 요약 한 줄 — 판정은 서버 파생값(overdueNoEvidence 등)과 minuteView 파생을
 * 그대로 읽고 여기서 재계산하지 않는다. 수치·근거·구간은 /minute 소관이라 여기선 실행 축과
 * "확인할 항목이 몇 건인가"까지만 말하고 손잡이를 건넨다(첫 화면은 사각을 없애는 게 목적). */
function MinuteSessionLine({ s }: { s: MinuteSession }) {
  const live = liveness(s);
  const list = issues(s, s.priceJobs);
  return (
    <p className="t-sm m-0">
      <b>
        {s.dataset}/{s.sourceGroup}
      </b>{' '}
      <StatusBadge tone={live.tone}>{live.label}</StatusBadge>{' '}
      {list.length === 0 ? (
        <span style={{ color: 'var(--fg-3)' }}>확인할 항목 없음</span>
      ) : (
        /* 무엇이 걸렸는지 이름까지만 — 건수·시각·근거는 드릴다운이 답한다 */
        <span style={{ color: 'var(--down)' }}>
          확인할 항목 {list.length}건 ({list.map((i) => i.short).join(' · ')})
        </span>
      )}
    </p>
  );
}

function MinuteLaneCard({ mockData }: { mockData?: MinuteStatus }) {
  const query = useMinuteStatus();
  const { isPending, isError } = query;
  const data = mockData ?? query.data;

  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">장중 1분 레인 {mockData && <MockChip />}</span>
        <Link to="/minute" className="t-xs">
          장중 1분 수집 상세 →
        </Link>
      </div>
      {!mockData && isError ? (
        /* 첫 화면 전체를 죽이지 않는다 — 이 카드만 실패를 밝힌다(조회 실패 ≠ 미가동) */
        <p className="t-xs m-0" style={{ color: 'var(--down, #b91c1c)' }}>
          1분 원장 조회 실패 — 미가동이 아니라 조회 오류입니다.
        </p>
      ) : !mockData && isPending ? (
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>불러오는 중…</p>
      ) : (
        <>
          {data!.sessions.length === 0 ? (
            <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
              오늘({data!.date}) 세션 없음 — 1분 파이프라인이 계획되지 않았다는 사실(비거래일 또는
              미가동)이지 오류가 아닙니다.
            </p>
          ) : (
            data!.sessions.map((s) => <MinuteSessionLine key={s.sessionId} s={s} />)
          )}
          {/* 뉴스 추출 DEAD — 세션(창 원장)과 별개 축이라 세션 유무와 무관하게 신호를 낸다
            * (ALPHA-697). 사유별 내역은 /lineage/news 의 추출 카드가 답한다. 이 카운트는
            * 서버가 data.date(오늘 KST)로 집계한 값이라 링크도 같은 날짜를 들고 내려간다 —
            * 날짜를 버리면 상세가 누적 사유를 보여줘 오늘 장애를 오진한다. */}
          {data!.newsJobs.dead > 0 && (
            <p className="t-xs m-0" style={{ color: 'var(--down, #b91c1c)' }}>
              뉴스 추출 DEAD {data!.newsJobs.dead}건 —{' '}
              <Link to={`/lineage/news?date=${data!.date}`}>사유별 내역</Link>
            </p>
          )}
        </>
      )}
    </div>
  );
}

export function OverviewPage() {
  const { data, isPending, isError, error } = useSourceOverview();

  if (isError) return <LoadError error={error} />;
  if (isPending) return <PageSkeleton rows={4} />;

  /* 레인이 없으면 원장의 구조(레인 → 필수 작업 → 결함)를 볼 수 없다 — 사실을 먼저 밝힌다 */
  if (data.lanes.length === 0) {
    return (
      <div className="flex flex-col gap-4">
        {/* 빈 원장은 정상 상태다(초기 환경) — 에러 화면이 아니다 */}
        <EmptyRealNotice>원장에 기록된 파이프라인 실행이 아직 없습니다.</EmptyRealNotice>
        <MockPreview>
          <OverviewBody data={MOCK_OVERVIEW} mock />
        </MockPreview>
      </div>
    );
  }

  return <OverviewBody data={data} />;
}

function OverviewBody({ data, mock = false }: { data: SourceOverview; mock?: boolean }) {
  return (
    <div className="flex flex-col gap-4">
      {data.lanes.map((lane) => (
        <LaneCard key={lane.pipelineType} lane={lane} mock={mock} />
      ))}

      {/* 장중 1분 레인 — EOD 레인만 보이면 장중 절반이 첫 화면의 사각이다(ALPHA-692, 멘토
       * "왜 전부 하루 주기냐"). ops 런 레인과 실행 모델이 달라 별도 카드로 요약만 얹는다. */}
      <MinuteLaneCard mockData={mock ? MOCK_MINUTE : undefined} />

      {/* 발행 분포는 이 콘솔의 경계 밖 — 없는 숫자를 지어내지 않고 소재만 밝힌다(계획 §6-1) */}
      <div className="card">
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
          설명 발행 분포(자동 제공·검수 대기·차단)는 증권사 관리 환경의 검수 콘솔에서 판정·집계됩니다
          — 이 화면은 파이프라인 원장이 답할 수 있는 범위까지만 표시합니다.
        </p>
      </div>
    </div>
  );
}
