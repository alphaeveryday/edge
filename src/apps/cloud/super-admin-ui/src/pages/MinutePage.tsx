/* 장중 1분 수집 — 운영 대시보드 (ALPHA-651 → ALPHA-738 재설계).
 *
 * 이 화면이 답하는 세 질문, 그 순서대로 배치한다:
 *   1. 지금 실행체가 살아 있는가        → 세션 헤더의 실행 배지
 *   2. 개입이 필요한 문제가 있는가      → 결함 사실 배지 · "현재 확인할 항목"
 *   3. 언제부터, 어느 정도, 근거는      → 창 구성 · 결손 구간 · 분별 근거 목록
 *
 * 분(1분)은 저장·증거의 grain 이지 기본 화면의 표현 grain 이 아니다. 그래서 분별 목록은
 * 접어 두고 구간으로 먼저 말한다 — 다만 근거 목록을 없애지는 않는다.
 *
 * 핵심 구분(관대화 금지): "안 돌았다"(무증거 — 기한 지난 DUE/CLAIMED, 서버 판정)와
 * "돌았는데 데이터 없음"(VALID_EMPTY — 거래 없는 분의 정상 결과)은 다른 사실이다.
 * MISSING 판정은 EOD QC 몫이라 장중 결손은 무증거 파생으로만 보인다.
 *
 * 판정은 전부 서버 것이거나 minuteView 의 순수 파생이다 — 화면에서 새 상태를 만들지 않는다.
 */
import { useState } from 'react';
import { PageSkeleton, StatusBadge } from 'ui-kit';
import type { BadgeTone } from 'ui-kit';
import type { MinuteJobCounts, MinuteSession, MinuteStatus } from '../domains/sources';
import {
  MINUTE_API_GAPS,
  evidencedCount,
  gapRuns,
  issues,
  liveness,
  materializedCount,
  qualityDefectCount,
  segments,
} from '../domains/sources/minuteView';
import type { Segment, ViewTone } from '../domains/sources/minuteView';
import { useMinuteStatus } from '../domains/sources/hooks';
import { MOCK_MINUTE } from '../mock/preview';
import { EmptyRealNotice, MockChip, MockPreview } from './_shared/MockPreview';
import { InfoPopover } from './_shared/InfoPopover';
import { LoadError } from './_shared/LoadError';
import '../styles/minute.css';

const tone = (t: ViewTone): BadgeTone => t;

const clock = (iso: string | null) =>
  iso ? new Date(iso).toLocaleTimeString('ko-KR', { timeZone: 'Asia/Seoul', hour12: false }) : '—';
const hhmm = (iso: string) =>
  new Date(iso).toLocaleTimeString('ko-KR', {
    timeZone: 'Asia/Seoul',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });

/* ══ 지표 타일 ══ */
function Kpi({
  label,
  value,
  sub,
  info,
  emphasis,
}: {
  label: string;
  value: string;
  sub?: string;
  info?: string;
  emphasis?: boolean;
}) {
  return (
    <div className="kpi">
      <div className="kpi-label">
        {label}
        {info && <InfoPopover text={info} label={label} />}
      </div>
      <div className="kpi-value" style={emphasis ? { color: 'var(--down)' } : undefined}>
        {value}
      </div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  );
}

/* ══ 창 구성 — 분별 전체 타임라인은 응답이 지원하지 않는다 ══ */
const SEG_CLASS: Record<Segment['pattern'], string> = {
  solid: 'mn-seg-solid',
  dot: 'mn-seg-dot',
  hatch: 'mn-seg-hatch',
  open: 'mn-seg-open',
};
const SEG_TONE_VAR: Record<ViewTone, string> = {
  active: 'var(--up)',
  warn: 'var(--warn)',
  blocked: 'var(--down)',
  neutral: 'var(--gray-300)',
  gated: 'var(--gray-400)',
  env: 'var(--accent)',
};

function SegmentBar({ session: s }: { session: MinuteSession }) {
  const segs = segments(s);
  const total = segs.reduce((a, x) => a + x.count, 0) || 1;
  return (
    <div>
      <div className="mn-bar" role="img" aria-label={segs.map((x) => `${x.label} ${x.count}창`).join(', ')}>
        {segs.map((x) => (
          <span
            key={x.key}
            className={`mn-seg ${SEG_CLASS[x.pattern]}`}
            style={{ width: `${(x.count / total) * 100}%`, ['--seg' as string]: SEG_TONE_VAR[x.tone] }}
          />
        ))}
      </div>
      {/* 색만으로 구분하지 않는다 — 무늬 + 이름 + 건수를 함께 낸다 */}
      <div className="mn-legend">
        {segs.map((x) => (
          <span key={x.key} className="mn-legend-item">
            <span
              className={`mn-swatch ${SEG_CLASS[x.pattern]}`}
              style={{ ['--seg' as string]: SEG_TONE_VAR[x.tone] }}
            />
            {x.label} <b>{x.count}</b>
            <InfoPopover text={x.meaning} label={x.label} />
          </span>
        ))}
      </div>
    </div>
  );
}

/* ══ 세션 ══ */
function SessionCard({ session: s, mock = false }: { session: MinuteSession; mock?: boolean }) {
  const [openEvidence, setOpenEvidence] = useState(false);
  const live = liveness(s);
  const w = s.windows;
  const list = issues(s, s.priceJobs);
  const runs = gapRuns(s.gaps);
  const materialized = materializedCount(s);

  return (
    <div className="card">
      {/* ── 1. 헤더 — 실행 축과 데이터 축을 나란히, 합성하지 않고 ── */}
      <div className="card-head mn-head">
        <span className="t-label">
          {s.dataset} / {s.sourceGroup} {mock && <MockChip />}
        </span>
        <span className="mn-badges">
          <StatusBadge tone={tone(live.tone)}>{live.label}</StatusBadge>
          <InfoPopover text={live.basis} label="실행 상태" title="실행 상태 판정 근거" />
          <span className="mn-axis-sep" aria-hidden="true" />
          {/* 데이터 축은 합성 판정을 만들지 않는다 — 응답이 준 사실 배지만 나열한다 */}
          {w.overdueNoEvidence > 0 && <StatusBadge tone="blocked">무증거 {w.overdueNoEvidence}</StatusBadge>}
          {w.incomplete > 0 && <StatusBadge tone="warn">불완전 {w.incomplete}</StatusBadge>}
          {w.invalid > 0 && <StatusBadge tone="blocked">무효 {w.invalid}</StatusBadge>}
          {w.missing > 0 && <StatusBadge tone="blocked">MISSING {w.missing}</StatusBadge>}
          {materialized !== s.expectedWindowCount && <StatusBadge tone="blocked">원장 불일치</StatusBadge>}
          {list.length === 0 && <StatusBadge tone="active">확인할 결함 없음</StatusBadge>}
        </span>
        <span className="t-xs mn-meta">
          phase {s.phase} · universe {s.universeVersion} · heartbeat {clock(s.heartbeatAt)}
        </span>
      </div>

      <div className="card-pad">
        {/* ── 2. 핵심 지표 ── */}
        <div className="mn-kpis">
          <Kpi
            label="증거 있는 창"
            value={`${evidencedCount(s)}창`}
            sub="정상 · 빈 데이터 · 불완전 · 무효"
            info={
              '실행 결과가 남은 창의 수다. 진행률이 아니다 — 이 응답은 "지금까지 도래한 창"을 정확히 세지 못한다(유효 lease 로 수집 중인 창이 무증거 집계에서 빠지기 때문). 없는 분모를 만들지 않는다.'
            }
          />
          <Kpi
            label="연속 완결"
            value={clock(s.contiguousCompleteThrough)}
            sub={`마지막 기록 ${clock(s.processedThrough)}`}
            info="이 시각까지는 빈 창 없이 연속으로 완결됐다는 워터마크다. 마지막 기록은 그 뒤로 개별 창이 더 있었다는 뜻일 뿐 연속 완결을 뜻하지 않는다."
          />
          <Kpi
            label="무증거"
            value={`${w.overdueNoEvidence}창`}
            emphasis={w.overdueNoEvidence > 0}
            sub="기한 경과 · 결과 없음"
            info="기한(window_end)이 지났는데 결과가 안 적힌 창 — 안 돌았거나 실행체가 죽었다. 서버(DB 시계) 판정이며 화면이 다시 계산하지 않는다. 돌았는데 거래가 없었던 창(정상 · 빈 데이터)과 다른 사실이다."
          />
          <Kpi
            label="품질 결함"
            value={`${qualityDefectCount(s)}창`}
            emphasis={qualityDefectCount(s) > 0}
            sub="불완전 · 무효 · MISSING"
            info="결과는 남았지만 정상이 아닌 창과 EOD QC 가 결손으로 판정한 창이다. 무증거는 여기 포함되지 않는다 — 증거의 유무가 다르다."
          />
        </div>

        {/* ── 3. 창 구성 ── */}
        <div className="mn-section">
          <div className="mn-section-head">
            <span className="t-label">창 구성</span>
            <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
              기대 창 {s.expectedWindowCount}개 기준
              <InfoPopover
                label="창 구성"
                title="왜 분별 타임라인이 아닌가"
                text={
                  '이 응답은 결함·무증거 창의 분별 행만 준다 — 정상 창과 미도래 창은 분별 행이 없다.\n' +
                  '그래서 390칸 타임라인은 만들 수 없고, 만들면 없는 데이터를 그린 가짜가 된다.\n' +
                  '대신 응답이 실제로 준 카운트만으로 구성을 그린다. 조각의 합은 언제나 기대 창 수다.'
                }
              />
            </span>
          </div>
          <SegmentBar session={s} />
        </div>

        {/* ── 4. 현재 확인할 항목 ── */}
        <div className="mn-section">
          <div className="mn-section-head">
            <span className="t-label">현재 확인할 항목</span>
            <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
              정상 창은 여기 오지 않습니다
            </span>
          </div>
          {list.length === 0 ? (
            <p className="t-sm m-0" style={{ color: 'var(--fg-3)' }}>
              확인할 예외가 없습니다 — 무증거 0 · 품질 결함 0 · 큐 고착 0 · 원장 불일치 없음.
            </p>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>항목</th>
                  <th className="num">건수</th>
                  <th>영향 시각</th>
                  <th>근거</th>
                </tr>
              </thead>
              <tbody>
                {list.map((i) => (
                  <tr key={i.key}>
                    <td>
                      <StatusBadge tone={tone(i.tone)}>{i.title}</StatusBadge>
                    </td>
                    <td className="num">
                      {i.count} <span className="col-muted t-xs">{i.unit}</span>
                    </td>
                    <td className="col-muted">
                      {i.range ? (
                        `${hhmm(i.range.from)} ~ ${hhmm(i.range.to)}`
                      ) : (
                        <span title="이 항목은 응답이 시각을 주지 않는다">시각 없음</span>
                      )}
                    </td>
                    <td className="col-muted t-xs">{i.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* ── 5. 소비자/큐 — 창과 grain 이 다르다 ── */}
        <div className="mn-section">
          <div className="mn-section-head">
            <span className="t-label">소비자 · 큐 — 가격 job</span>
            <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
              단위 = 논리 job (창 집계와 다른 축)
            </span>
          </div>
          <JobRow jobs={s.priceJobs} />
        </div>

        {/* ── 6. 분별 근거 — 접어 둔다. 없애지는 않는다 ── */}
        <div className="mn-section">
          <button
            type="button"
            className="mn-disclosure"
            aria-expanded={openEvidence}
            onClick={() => setOpenEvidence((v) => !v)}
          >
            {openEvidence ? '▾' : '▸'} 분별 근거 — 결손 · 무증거 창 {s.gaps.length}개 ({runs.length}구간)
          </button>
          {openEvidence &&
            (s.gaps.length === 0 ? (
              <p className="t-sm m-0" style={{ color: 'var(--fg-3)', marginTop: 8 }}>
                결손·무증거 창이 없습니다. 정상 창의 분별 행은 이 응답에 포함되지 않습니다.
              </p>
            ) : (
              <>
                <table className="table" style={{ marginTop: 8 }}>
                  <thead>
                    <tr>
                      <th>구간(KST)</th>
                      <th className="num">창</th>
                      <th>원장 상태</th>
                      <th>판정</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.map((r) => (
                      <tr key={`${r.from}-${r.dataStatus}-${r.noEvidence}`}>
                        <td className="num">
                          {hhmm(r.from)} ~ {hhmm(r.to)}
                        </td>
                        <td className="num">{r.count}</td>
                        <td className="mono col-muted">{r.dataStatus}</td>
                        <td>
                          {r.noEvidence ? (
                            <StatusBadge tone="blocked">무증거 (기한 경과 · 결과 없음)</StatusBadge>
                          ) : (
                            <StatusBadge tone="warn">증거 있는 결함</StatusBadge>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 6 }}>
                  위는 <b>원장 증거</b>(창 상태·시각)입니다. 그 시각의 <b>실행 로그</b>는 별개 축이며 이
                  응답에 로그 조회 경로가 없어 아직 배선되지 않았습니다 — 원장 행을 "로그"라고 부르지
                  않습니다.
                </p>
              </>
            ))}
        </div>
      </div>
    </div>
  );
}

function JobRow({ jobs }: { jobs: MinuteJobCounts }) {
  return (
    <div className="mn-jobs">
      <span>
        대기 <b>{jobs.waiting}</b>
      </span>
      <span>
        처리 중 <b>{jobs.claimed}</b>
      </span>
      <span style={jobs.claimedExpired > 0 ? { color: 'var(--down)' } : undefined}>
        그중 유효 lease 없음 <b>{jobs.claimedExpired}</b>
        <InfoPopover
          label="유효 lease 없는 claim"
          text="Consumer 가 죽고 아무도 재청구하지 않은 고착 후보다(만료·NULL 포함 — writer 의 회수 조건과 같은 집합). 서버 판정이며 '처리 중'에 뭉개지 않는다."
        />
      </span>
      <span>
        성공 <b>{jobs.succeeded}</b>
      </span>
      <span style={jobs.dead > 0 ? { color: 'var(--warn)' } : undefined}>
        DEAD <b>{jobs.dead}</b>
        <InfoPopover
          label="DEAD job"
          text="재시도가 소진된 job 이다. 이 응답에는 해소 축이 없어 이미 복구됐는지 알 수 없다 — 당일 누적이며 그것만으로 지금 장애라고 단정하지 않는다."
        />
      </span>
    </div>
  );
}

/* ══ 페이지 ══ */
export function MinutePage() {
  /* 기본은 오늘(서버 KST) — 세션은 하루 단위 identity 다 */
  const [date, setDate] = useState<string>('');
  const { data, isPending, isError, error } = useMinuteStatus(date || undefined);

  if (isError) return <LoadError error={error} />;
  if (isPending) return <PageSkeleton rows={6} />;

  const j = data.newsJobs;
  /* 세션도 job 도 전무해야 "볼 것이 없는 화면"이다 — job 만 있어도 실데이터가 있는 것이다 */
  const empty =
    data.sessions.length === 0 &&
    j.waiting + j.claimed + j.claimedExpired + j.succeeded + j.dead === 0;

  if (empty) {
    return (
      <div className="flex flex-col gap-4">
        <EmptyRealNotice>
          이 날짜({data.date})의 세션이 없습니다 — 1분 파이프라인이 계획되지 않았다는 사실이다(비거래일
          또는 미가동). 오류가 아니라 관측 결과다. 뉴스 추출 job 도 0건이다.
        </EmptyRealNotice>
        <MockPreview>
          <MinuteBody data={MOCK_MINUTE} date={date} setDate={setDate} mock />
        </MockPreview>
      </div>
    );
  }

  return <MinuteBody data={data} date={date} setDate={setDate} />;
}

function MinuteBody({
  data,
  date,
  setDate,
  mock = false,
}: {
  data: MinuteStatus;
  date: string;
  setDate: (v: string) => void;
  mock?: boolean;
}) {
  return (
    <div className="flex flex-col gap-4">
      <div className="card card-pad mn-toolbar">
        <span className="t-sm">
          세션 날짜(KST) <b>{data.date}</b> {mock && <MockChip />}
        </span>
        <input
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
          className="t-xs"
          aria-label="세션 날짜"
          style={{ border: '1px solid var(--border-strong)', borderRadius: 4, padding: '2px 6px' }}
        />
        <span style={{ flex: 1 }} />
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          60초마다 자동 갱신 · 실행 축 · 창 축 · job 축은 서로 다른 축입니다
          <InfoPopover
            label="상태 기준"
            title="상태 기준"
            text={
              '실행 축 — 세션이 살아 있는가(phase + lease). 종료 국면의 lease 만료는 정상이다.\n' +
              '창 축 — 1분 창의 데이터 상태. 무증거(기한 지났는데 결과 없음)와 정상 · 빈 데이터(돌았는데 거래 없음)는 다른 사실이다.\n' +
              'job 축 — 논리 job 큐. 창과 grain 이 다르므로 창 집계와 합치지 않는다.\n\n' +
              '무증거 · lease 만료 · claimed 만료는 모두 서버(DB 시계) 판정이라 화면이 다시 계산하지 않는다.\n' +
              'MISSING 은 EOD QC 판정이라 장중에는 매겨지지 않는다 — 장중 결손은 무증거로만 보인다.'
            }
          />
        </span>
      </div>

      {data.sessions.length === 0 ? (
        <div className="card card-pad">
          <p className="t-sm m-0">이 날짜의 세션이 없습니다.</p>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
            1분 파이프라인이 계획되지 않았다는 사실입니다(비거래일 또는 미가동) — 오류가 아니라 관측
            결과입니다. 아래 뉴스 추출 job 은 세션과 다른 축이라 따로 집계됩니다.
          </p>
        </div>
      ) : (
        data.sessions.map((s) => <SessionCard key={s.sessionId} session={s} mock={mock} />)
      )}

      {/* 뉴스 job — 세션 연결 컬럼이 없어 날짜 축의 별도 원장이다 */}
      <div className="card">
        <div className="card-head">
          <span className="t-label">소비자 · 큐 — 뉴스 추출 job {mock && <MockChip />}</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            기사 단위 논리 job · 날짜 축 = job 생성 시각(KST)
            <InfoPopover
              label="뉴스 job 축"
              text="뉴스 job 은 세션 연결 컬럼이 없어(기사 identity 기반) 세션이 아니라 날짜로 집계된다. 가격 job 과도, 1분 창과도 다른 축이라 합쳐 세지 않는다."
            />
          </span>
        </div>
        <div className="card-pad">
          <JobRow jobs={data.newsJobs} />
        </div>
      </div>

      {/* 못 그리는 것을 조용히 넘기지 않는다 — 부채를 화면에 남긴다 */}
      <details className="card">
        <summary className="t-sm mn-summary">
          이 화면이 더 정확해지려면 필요한 API 확장 {MINUTE_API_GAPS.length}건
        </summary>
        <div className="card-pad" style={{ paddingTop: 0 }}>
          <ul className="t-xs" style={{ color: 'var(--fg-3)', margin: 0, paddingLeft: 18 }}>
            {MINUTE_API_GAPS.map((g) => (
              <li key={g.need} style={{ marginBottom: 4 }}>
                <b style={{ color: 'var(--fg-2)' }}>{g.need}</b> — {g.why}
              </li>
            ))}
          </ul>
        </div>
      </details>
    </div>
  );
}
