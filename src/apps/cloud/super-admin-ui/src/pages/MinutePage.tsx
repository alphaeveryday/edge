/* 장중 세션 — 데이터셋별 운영 상태 (ALPHA-651 → ALPHA-738 재설계).
 *
 * **장중은 판정 단위가 아니라 실행 시간대다.** 1분 가격(price_minute)과 뉴스(news_minute)는
 * 같은 세션·창 원장을 쓰지만 서로 독립한 데이터셋이고, 자기 기준으로 상태를 가진다. 그래서
 * 이 화면은 둘을 합친 종합 정상/장애를 만들지 않는다 — 공통 영역은 거래일·기준 시각까지고,
 * 본문은 선택한 데이터셋의 사실로 통째로 바뀐다.
 *
 * 데이터셋마다 답하는 질문:
 *   1분 가격 — 거래시간의 기대 수집 창이 결과 증거로 귀결됐는가
 *   뉴스     — 1분 간격 poll 이 예정대로 돌았고, 관측이 뒤처지지 않았는가
 *
 * 같은 컬럼이 다른 사실을 말한다(minuteView 의 NEWS_SEGMENTS 참고): 가격의 VALID_EMPTY 는
 * "거래가 없었다", 뉴스의 VALID_EMPTY 는 "신규 기사가 없었다"이고, 가격의 INCOMPLETE 는
 * unit 유실, 뉴스의 INCOMPLETE 는 poll 이 anchor 에 못 닿고 잘린 것(따라잡기 예약)이다.
 *
 * 공통으로 지키는 것: "안 돌았다"(무증거 — 기한 지난 DUE/CLAIMED, 서버 판정)와 "돌았는데
 * 결과가 비었다"(VALID_EMPTY)는 끝까지 다른 사실이다. 판정은 전부 서버 것이거나 minuteView
 * 의 순수 파생이다 — 화면에서 새 상태를 만들지 않는다.
 */
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { PageSkeleton, StatusBadge } from 'ui-kit';
import type { BadgeTone } from 'ui-kit';
import type { MinuteJobCounts, MinuteSession, MinuteStatus, OverviewLane } from '../domains/sources';
import {
  MINUTE_API_GAPS,
  NEWS_API_GAPS,
  datasetKind,
  evidencedCount,
  gapRuns,
  healthyClaimed,
  hasNoSignal,
  isCurrentKstDate,
  isPollLane,
  issues,
  liveness,
  materializedCount,
  qualityDefectCount,
  segments,
  sessionsForSourceGroup,
} from '../domains/sources/minuteView';
import type { ApiGap, Issue, Segment, ViewTone } from '../domains/sources/minuteView';
import { useMinuteStatus, useSourceOverview } from '../domains/sources/hooks';
import { MOCK_OVERVIEW } from '../mock/preview';
import { EmptyRealNotice, MockChip } from './_shared/MockPreview';
import { REALTIME_DATASETS, RUN_DETAIL_UNAVAILABLE } from './ops/investigation';
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

/* 데이터셋 표시 이름 — 어휘 정본은 data_pipeline/minute/states.py 다. 여기 없는 dataset 은
 * 원문 그대로 쓴다(모르는 것에 이름을 지어 주지 않는다). */
const DATASET_LABEL: Record<string, string> = {
  price_minute: '1분 가격',
  news_minute: '뉴스',
};
const DATASET_ORDER = ['price_minute', 'news_minute'];

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
/* ══ 창·poll 구성 — 분별 전체 타임라인은 응답이 지원하지 않는다 ══ */
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

function SegmentBar({ session: s, unit }: { session: MinuteSession; unit: string }) {
  const segs = segments(s);
  const total = segs.reduce((a, x) => a + x.count, 0) || 1;
  return (
    <div>
      <div className="mn-bar" role="img" aria-label={segs.map((x) => `${x.label} ${x.count}${unit}`).join(', ')}>
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

/* ══ 현재 확인할 항목 — 두 데이터셋이 같은 표를 쓰되 내용은 각자의 판정이다 ══ */
function IssueTable({ list }: { list: Issue[] }) {
  if (list.length === 0) {
    return (
      <p className="t-sm m-0" style={{ color: 'var(--fg-3)' }}>
        확인할 예외가 없습니다 — 무증거 0 · 품질 결함 0 · 원장 불일치 없음.
      </p>
    );
  }
  return (
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
  );
}

/* ══ 분별 근거 — 문제가 있는 구간에서 그 원장 행까지 내려간다 ══ */
function GapEvidence({
  session: s,
  noun,
  date,
}: {
  session: MinuteSession;
  noun: string;
  date: string;
}) {
  const [open, setOpen] = useState(false);
  const runs = gapRuns(s.gaps);
  return (
    <div className="mn-section">
      <button
        type="button"
        className="mn-disclosure"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? '▾' : '▸'} 분별 근거 — 결손 · 무증거 {noun} {s.gaps.length}개 ({runs.length}구간)
      </button>
      {open &&
        (s.gaps.length === 0 ? (
          <p className="t-sm m-0" style={{ color: 'var(--fg-3)', marginTop: 8 }}>
            결손·무증거 {noun}이 없습니다. 정상 {noun}의 분별 행은 이 응답에 포함되지 않습니다.
          </p>
        ) : (
          <>
            <table className="table" style={{ marginTop: 8 }}>
              <thead>
                <tr>
                  <th>구간(KST)</th>
                  <th className="num">{noun}</th>
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
              위는 <b>원장 증거</b>({noun} 상태·시각)입니다. 그 시각의 <b>실행 로그</b>는 별개 축이며 이
              응답에 로그 조회 경로가 없어 아직 배선되지 않았습니다 — 원장 행을 "로그"라고 부르지
              않습니다.
            </p>
            {/* 사건·실행·세션이 같은 원장 근거 화면을 쓴다 — 화면마다 원장 UI 를 만들지 않는다 */}
            <p className="t-xs m-0" style={{ marginTop: 6 }}>
              {/* 벤더(source_group)를 반드시 싣는다 — 세션 identity 는 `(dataset, sourceGroup, date)`
                  라, 이 축을 버리면 원장 화면이 `ofDataset[0]` 으로 **남의 벤더 세션 행**
                  (sessionId·phase·lease)을 아무 경고 없이 세운다. 사건 경로(`investigate`)는
                  이미 싣고 있는데 여기가 두 번째 생산자였다. */}
              <Link
                to={`/sources?dataset=${encodeURIComponent(s.dataset)}&date=${encodeURIComponent(date)}&sourceGroup=${encodeURIComponent(s.sourceGroup)}`}
              >
                원장 근거 보기 →
              </Link>{' '}
              <span style={{ color: 'var(--fg-3)' }}>
                세션 행·{noun} 상태 집계·결손 행 원문을 이 세션 문맥으로 좁혀 봅니다
              </span>
            </p>
          </>
        ))}
    </div>
  );
}

/* ══ 1분 가격 세션 ══ */
function PriceSessionCard({
  session: s,
  date,
  mock = false,
}: {
  session: MinuteSession;
  date: string;
  mock?: boolean;
}) {
  const live = liveness(s);
  const w = s.windows;
  const list = issues(s, s.priceJobs);
  const materialized = materializedCount(s);

  return (
    <div className="card">
      {/* ── 헤더 — 실행 축과 데이터 축을 나란히, 합성하지 않고 ── */}
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
            info={
              '판정 — 기한(window_end) 경과 후 결과 증거 없음\n' +
              '원장 상태 — DUE 또는 유효 lease 없는 CLAIMED\n' +
              '판정 주체 — 서버(DB 시계). 화면이 다시 계산하지 않는다\n' +
              '구분 — 빈 데이터(VALID_EMPTY)는 실행 증거가 있어 정상 귀결로 따로 집계한다\n' +
              '다음 확인 — 세션 heartbeat · lease · 관련 job\n\n' +
              '이 사실만으로 미실행·실행체 사망을 확정하지 않는다.'
            }
          />
          <Kpi
            label="품질 결함"
            value={`${qualityDefectCount(s)}창`}
            emphasis={qualityDefectCount(s) > 0}
            sub="불완전 · 무효 · MISSING"
            info="결과는 남았지만 정상이 아닌 창과 EOD QC 가 결손으로 판정한 창이다. 무증거는 여기 포함되지 않는다 — 증거의 유무가 다르다."
          />
        </div>

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
          <SegmentBar session={s} unit="창" />
        </div>

        <div className="mn-section">
          <div className="mn-section-head">
            <span className="t-label">현재 확인할 항목</span>
            <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
              정상 창은 여기 오지 않습니다
            </span>
          </div>
          <IssueTable list={list} />
        </div>

        {/* 소비자/큐 — 창과 grain 이 다르다 */}
        <div className="mn-section">
          <div className="mn-section-head">
            <span className="t-label">후속 처리 작업 — 가격 window job</span>
            <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
              DB job 원장 상태 집계 · 창 집계와 다른 축
              <InfoPopover label="처리 job 축" title="처리 job 상태 · DB 원장" text={JOB_TIP} />
            </span>
          </div>
          <JobRow jobs={s.priceJobs} />
        </div>

        <GapEvidence session={s} noun="창" date={date} />
      </div>
    </div>
  );
}

const NO_JOB_AXIS: MinuteJobCounts = { waiting: 0, claimed: 0, claimedExpired: 0, succeeded: 0, dead: 0 };

/** 가격·뉴스 전용 job 의미가 없는 실시간 레인. 세션·창/poll 원장만 그대로 보인다. */
function GenericSessionCard({
  session: s,
  date,
  mock = false,
}: {
  session: MinuteSession;
  date: string;
  mock?: boolean;
}) {
  const live = liveness(s);
  const kind = datasetKind(s.dataset);
  const poll = isPollLane(kind);
  const noun = poll ? 'poll' : '창';
  const list = issues(s, NO_JOB_AXIS);
  return (
    <div className="card">
      <div className="card-head mn-head">
        <span className="t-label">{s.dataset} / {s.sourceGroup} {mock && <MockChip />}</span>
        <StatusBadge tone={tone(live.tone)}>{live.label}</StatusBadge>
        <InfoPopover text={live.basis} label="실행 상태" title="실행 상태 판정 근거" />
        <span className="t-xs mn-meta">phase {s.phase} · heartbeat {clock(s.heartbeatAt)}</span>
      </div>
      <div className="card-pad">
        <div className="mn-section">
          <div className="mn-section-head">
            <span className="t-label">{noun} 구성</span>
            <span className="t-xs" style={{ color: 'var(--fg-3)' }}>예정 {s.expectedWindowCount}{poll ? '회' : '개'} 기준</span>
          </div>
          <SegmentBar session={s} unit={poll ? '회' : '창'} />
        </div>
        <div className="mn-section">
          <div className="mn-section-head"><span className="t-label">현재 확인할 항목</span></div>
          <IssueTable list={list} />
        </div>
        <GapEvidence session={s} noun={noun} date={date} />
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
          이 데이터셋에는 이 응답이 제공하는 후속 처리 job 축이 없습니다.
        </p>
      </div>
    </div>
  );
}

/* ══ 뉴스 세션 — 1분 간격 poll ══
 *
 * 가격에서 그대로 가져오지 않은 것: universe(뉴스 세션은 소스 단위라 원장에 'none' 이
 * 박힌다) · 연속 완결 워터마크(poll 축에서 읽히는 값이 아니라 최근 성공 poll 의 보조
 * 정보로 내린다) · 가격 job(뉴스 세션에는 window job 이 없다).
 */
function NewsSessionCard({
  session: s,
  date,
  mock = false,
}: {
  session: MinuteSession;
  date: string;
  mock?: boolean;
}) {
  const live = liveness(s);
  const w = s.windows;
  /* 뉴스 세션에는 세션 축 job 이 없다(price_window_job 은 가격 창을 참조한다) — 서버가
   * 0 을 채워 보내며, 기사 단위 추출 job 은 날짜 축이라 아래 별도 카드에서 본다. */
  const list = issues(s, s.priceJobs);
  const materialized = materializedCount(s);

  return (
    <div className="card">
      <div className="card-head mn-head">
        <span className="t-label">
          {s.dataset} / {s.sourceGroup} {mock && <MockChip />}
        </span>
        <span className="mn-badges">
          <StatusBadge tone={tone(live.tone)}>{live.label}</StatusBadge>
          <InfoPopover text={live.basis} label="실행 상태" title="실행 상태 판정 근거" />
          <span className="mn-axis-sep" aria-hidden="true" />
          {w.overdueNoEvidence > 0 && <StatusBadge tone="blocked">무증거 poll {w.overdueNoEvidence}</StatusBadge>}
          {w.incomplete > 0 && <StatusBadge tone="warn">잘린 poll {w.incomplete}</StatusBadge>}
          {w.invalid > 0 && <StatusBadge tone="blocked">격리 {w.invalid}</StatusBadge>}
          {w.missing > 0 && <StatusBadge tone="blocked">MISSING {w.missing}</StatusBadge>}
          {materialized !== s.expectedWindowCount && <StatusBadge tone="blocked">원장 불일치</StatusBadge>}
          {list.length === 0 && <StatusBadge tone="active">확인할 결함 없음</StatusBadge>}
        </span>
        <span className="t-xs mn-meta">
          phase {s.phase} · heartbeat {clock(s.heartbeatAt)}
        </span>
      </div>

      <div className="card-pad">
        <div className="mn-kpis">
          <Kpi
            label="결과가 남은 poll"
            value={`${evidencedCount(s)}회`}
            sub="신규 관측 · 신규 0건 · 잘림 · 격리"
            info={
              'poll 이 돌고 결과가 원장에 적힌 분의 수다. 진행률이 아니다 — 이 응답은 "지금까지 도래한 분"을 정확히 세지 못한다(유효 lease 로 poll 중인 분이 무증거 집계에서 빠지기 때문).'
            }
          />
          <Kpi
            label="최근 성공 poll"
            value={clock(s.processedThrough)}
            sub={`연속 완결 ${clock(s.contiguousCompleteThrough)}`}
            info="결과가 기록된 가장 최신 분이다. 그 앞에 구멍이 있어도 전진하므로 '여기까지 다 됐다'는 뜻이 아니다 — 구멍 없이 연속으로 완결된 지점은 보조로 적은 연속 완결 시각이다."
          />
          <Kpi
            label="무증거 poll"
            value={`${w.overdueNoEvidence}회`}
            emphasis={w.overdueNoEvidence > 0}
            sub="기한 경과 · 결과 없음"
            info={
              '판정 — 기한(window_end) 경과 후 결과 증거 없음\n' +
              '원장 상태 — DUE 또는 유효 lease 없는 CLAIMED (서버 DB 시계 판정)\n' +
              '구분 — 신규 0건(VALID_EMPTY)은 poll 실행 증거가 있어 정상 귀결로 따로 집계한다\n' +
              '다음 확인 — 세션 heartbeat · lease · 관련 job\n\n' +
              '기사가 없었다는 뜻이 아니고, 이 사실만으로 worker 사망을 단정하지도 않는다.\n' +
              '한계: 벤더 차단 쿨다운 중에는 Worker 가 claim 을 반납해 DUE 로 돌아가므로, 억제 중인 분도 기한이 지나면 여기 섞인다 — 응답에 차단 상태 축이 없다.'
            }
          />
          <Kpi
            label="잘린 poll (따라잡기)"
            value={`${w.incomplete}회`}
            emphasis={w.incomplete > 0}
            sub="anchor 미도달 · 관측 뒤처짐"
            info="page 예산 안에 직전 성공 anchor 까지 못 읽고 끝난 분이다(truncated). 다음 poll 이 더 깊은 예산으로 따라잡도록 예약된 상태이며, 성공으로 위장하지 않는다. 지금도 뒤처져 있는지는 anchor 상태가 응답에 없어 이 카운트로 간접 관측할 뿐이다."
          />
        </div>

        <div className="mn-section">
          <div className="mn-section-head">
            <span className="t-label">poll 구성</span>
            <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
              예정 poll {s.expectedWindowCount}회 기준 · 1분 간격
              <InfoPopover
                label="poll 구성"
                title="뉴스는 왜 이 축인가"
                text={
                  '뉴스 수집은 1분마다 소스를 poll 하는 방식이고, 그 예정·귀결이 가격과 같은 창 원장에 기록된다.\n' +
                  '그래서 축은 같지만 사실이 다르다 — 신규 0건은 정상 결과이고, 잘린 poll 은 관측이 뒤처졌다는 신호다.\n' +
                  '"다음 예정 poll"은 그릴 수 없다: 이 응답은 결함·무증거 분의 행만 주고 미도래 분의 행은 주지 않는다.'
                }
              />
            </span>
          </div>
          <SegmentBar session={s} unit="회" />
        </div>

        <div className="mn-section">
          <div className="mn-section-head">
            <span className="t-label">현재 확인할 항목</span>
            <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
              정상 poll 은 여기 오지 않습니다
            </span>
          </div>
          <IssueTable list={list} />
        </div>

        <GapEvidence session={s} noun="poll" date={date} />
      </div>
    </div>
  );
}

/**
 * 후속 처리 작업 — **DB job 원장의 상태 집계다. 실제 큐 지표가 아니다.**
 *
 * 세는 값은 전부 job 행의 `status` 와 `lease_expires_at` 이다(price_window_job ·
 * news_extraction_job). SQS 의 visible/in-flight, oldest message age, consumer heartbeat 는
 * 어느 응답에도 없다 — 그래서 "소비자 · 큐"라고 부르지 않는다.
 *
 * 순서는 조치 판단 순이다: 미귀결 → 확인 필요 → 누적. 누적 성공을 앞에 두면 "1,284 성공"이
 * 먼저 읽혀 고착 1건이 묻힌다.
 */
const JOB_TIP = [
  '이 숫자는 DB job 원장(status 컬럼)의 집계다 — 실제 큐 지표가 아니다.',
  'SQS backlog · in-flight · oldest message age 는 이 응답에 없다.',
  '',
  '대기 = PENDING + RETRY_WAIT',
  '처리 중 = CLAIMED',
  '유효 lease 없는 CLAIMED = status 는 CLAIMED 인데 lease 가 만료됐거나 NULL 이다.',
  '  writer 의 재청구 조건과 같은 집합이라 고착 후보이지만 **consumer 사망 확정이 아니다**.',
  'DEAD = 재시도가 끝난 상태. 이 응답에 해소 축이 없어 **당일 누적**이다',
  '  (이미 복구됐는지 알 수 없으므로 그것만으로 지금 장애라고 단정하지 않는다).',
].join('\n');

function JobRow({ jobs }: { jobs: MinuteJobCounts }) {
  const unresolved = jobs.waiting + jobs.claimed;
  return (
    <div className="mn-jobgroups">
      <div className="mn-jobgroup">
        <span className="t-label">
          미귀결 <b>{unresolved}</b>
        </span>
        <span className="t-xs" style={{ color: 'var(--fg-2)' }}>
          대기 · 재시도 대기 <b>{jobs.waiting}</b> · 유효 처리 중 <b>{healthyClaimed(jobs)}</b>
        </span>
      </div>
      <div className="mn-jobgroup">
        <span className="t-label">확인 필요</span>
        <span
          className="t-xs"
          style={{ color: jobs.claimedExpired > 0 ? 'var(--down)' : 'var(--fg-2)' }}
        >
          유효 lease 없는 CLAIMED <b>{jobs.claimedExpired}</b>
        </span>
        <span className="t-xs" style={{ color: jobs.dead > 0 ? 'var(--warn)' : 'var(--fg-2)' }}>
          DEAD <b>{jobs.dead}</b> · 당일 누적
        </span>
      </div>
      <div className="mn-jobgroup mn-jobgroup-muted">
        <span className="t-label">누적</span>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          성공 <b>{jobs.succeeded}</b>
        </span>
      </div>
    </div>
  );
}

/* ══ 뉴스 추출 job — 세션이 아니라 날짜 축이다 ══ */
function NewsJobsCard({ jobs, date, mock }: { jobs: MinuteJobCounts; date: string; mock: boolean }) {
  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">후속 처리 작업 — 뉴스 추출 job {mock && <MockChip />}</span>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          기사 단위 논리 job · 날짜 축 = job 생성 시각(KST)
          <InfoPopover
            label="뉴스 추출 job 축"
            title="처리 job 상태 · DB 원장"
            text={
              '뉴스 추출 job(news_extraction_job)은 기사 identity 기반이라 세션 연결 컬럼이 없다 — 세션이 아니라 날짜로 집계된다.\n' +
              '그래서 이 숫자에는 장중 세션이 만든 job 과 백필 생산자가 만든 job 이 섞여 있다. 위 poll 집계와 다른 축이라 합쳐 세지 않는다.\n\n' +
              JOB_TIP
            }
          />
        </span>
      </div>
      <div className="card-pad">
        <JobRow jobs={jobs} />
        <p className="t-xs m-0" style={{ marginTop: 8 }}>
          <Link to={`/lineage/news?date=${date}`}>
            뉴스 계보에서 이 날짜의 추출 상세 보기 →
          </Link>
          <span style={{ color: 'var(--fg-3)' }}> — 사유별 DEAD 와 문서 목록은 그 화면 소관입니다.</span>
        </p>
      </div>
    </div>
  );
}

/* ══ 부채 목록 ══ */
function ApiGapList({ gaps }: { gaps: ApiGap[] }) {
  return (
    <details className="card">
      <summary className="t-sm mn-summary">
        이 데이터셋 화면이 더 정확해지려면 필요한 API 확장 {gaps.length}건
      </summary>
      <div className="card-pad" style={{ paddingTop: 0 }}>
        <ul className="t-xs" style={{ color: 'var(--fg-3)', margin: 0, paddingLeft: 18 }}>
          {gaps.map((g) => (
            <li key={g.need} style={{ marginBottom: 4 }}>
              <b style={{ color: 'var(--fg-2)' }}>{g.need}</b> — {g.why}
            </li>
          ))}
        </ul>
      </div>
    </details>
  );
}

/* ══ 실행 중인 배치 ══
 *
 * 이 화면이 답하는 질문이 "지금 무엇이 가동 중인가"라서 실시간 수집 세션만으로는 반쪽이다 —
 * 도는 배치 실행도 함께 세운다. **다만 합산하지 않는다**: 배치와 세션은 원장도 판정 축도
 * 달라서 하나의 점수로 접으면 어느 쪽이 문제인지 사라진다.
 *
 * 값은 레인 원장 요약(/sources/overview)이 준 것뿐이다. 시작 시각·경과·heartbeat 는 그 응답에
 * 없어 지어내지 않고 계측 없음으로 둔다(계획 시각까지만 사실이다).
 */
const RUNNING_STATUSES = new Set(['RUNNING']);

function BatchRunning({ historical }: { historical: boolean }) {
  const { data, isPending, isError } = useSourceOverview();
  /* 과거 날짜를 보고 있으면 "지금 도는 배치"를 섞지 않는다 — 이 블록은 현재 시점의 사실이다 */
  if (historical) return null;

  const lanes = data?.lanes ?? [];
  const live = lanes.filter(
    (l) => !l.notToday && (RUNNING_STATUSES.has(l.orchestrationStatus ?? '') || l.opsStatus === 'IN_PROGRESS'),
  );
  const mock = data === undefined && !isPending;
  const view: OverviewLane[] = mock
    ? MOCK_OVERVIEW.lanes.filter(
        (l) => RUNNING_STATUSES.has(l.orchestrationStatus ?? '') || l.opsStatus === 'IN_PROGRESS',
      )
    : live;

  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">실행 중인 배치</span>
        {mock && <MockChip />}
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          레인 원장 요약 기준 · 아래 수집 세션과 합산하지 않습니다
          <InfoPopover
            label="실행 중인 배치"
            title="실행 중인 배치"
            text={
              '레인별 최신 런 중 오케스트레이션이 RUNNING 이거나 운영 상태가 진행 중인 것이다.\n\n' +
              '시작 시각·경과 시간·heartbeat 는 이 응답에 없다 — 계획 시각까지만 사실이라 나머지는\n' +
              '계측 없음으로 둔다(경과를 계획 시각에서 역산하면 실제 시작과 다른 축을 지어내는 것이다).\n\n' +
              '완료/전체는 **필수(required) DUE 작업** 기준이다.'
            }
          />
        </span>
      </div>
      <div className="card-pad">
        {isError && data && (
          <p className="t-xs m-0" style={{ color: 'var(--warn)', marginBottom: 8 }}>
            레인 요약 재조회에 실패했습니다 — 직전 실측을 유지합니다.
          </p>
        )}
        {isPending ? (
          <p className="t-sm m-0" style={{ color: 'var(--fg-3)' }}>
            불러오는 중…
          </p>
        ) : view.length === 0 ? (
          <p className="t-sm m-0" style={{ color: 'var(--fg-3)' }}>
            지금 도는 배치 실행이 없습니다 — 오류가 아니라 관측 결과입니다.
          </p>
        ) : (
          <>
            {mock && (
              <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginBottom: 8 }}>
                {isError
                  ? '레인 요약을 불러오지 못했습니다 — 아래는 목데이터입니다.'
                  : '레인 요약을 불러오지 못해 화면 구조 확인용 목데이터를 보여줍니다.'}
              </p>
            )}
            <ul className="mn-runlist">
              {view.map((l) => (
                <li key={l.runKey}>
                  <div className="mn-runcard">
                    <span className="t-label">{l.pipelineType}</span>
                    <StatusBadge tone={l.opsStatus === 'DEGRADED' ? 'warn' : 'env'}>
                      {l.orchestrationStatus ?? l.opsStatus}
                    </StatusBadge>
                    <span className="mono t-xs" style={{ color: 'var(--fg-3)' }}>
                      {l.runKey}
                    </span>
                    <span className="t-xs" style={{ color: 'var(--fg-2)' }}>
                      계획 {l.plannedAt ? clock(l.plannedAt) : '—'} · 완료 {l.counts.fulfilled}/
                      {l.counts.requiredDue} · 남은 작업 {l.counts.pending}
                    </span>
                    <span className="t-xs" style={{ color: 'var(--fg-4)' }}>
                      시작·경과·heartbeat 계측 없음
                    </span>
                    <span className="t-xs mn-runcard-go">{RUN_DETAIL_UNAVAILABLE}</span>
                  </div>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}

/* ══ 페이지 ══
 *
 * 날짜·데이터셋은 **URL 이 정본**이다(`/minute?date=2026-08-03&dataset=news_minute`) —
 * 실행 이력이 특정 날짜·데이터셋을 지목해 보낼 수 있고 새로고침해도 선택이 남는다.
 * 갱신은 `replace` 다: 탭을 누를 때마다 히스토리를 쌓으면 뒤로 가기가 이 화면 안에서
 * 맴돌아 **온 곳(그날 실행 목록)으로 못 돌아간다**.
 */
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export function MinutePage() {
  const [params, setParams] = useSearchParams();
  /* 형식이 어긋난 date 는 서버로 넘기지 않고 기본값(오늘, 서버 KST)으로 정규화한다 */
  const raw = params.get('date') ?? '';
  const date = DATE_RE.test(raw) ? raw : '';
  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  const setDate = (v: string) => setParam('date', v);
  const sourceGroup = params.get('sourceGroup')?.trim() || undefined;
  const setDataset = (v: string) => {
    const next = new URLSearchParams(params);
    next.set('dataset', v);
    next.delete('sourceGroup');
    setParams(next, { replace: true });
  };
  const { data, isPending, isError, error, dataUpdatedAt } = useMinuteStatus(date || undefined);

  if (isError && !data) return <LoadError error={error} />;
  if (isPending) return <PageSkeleton rows={6} />;

  /* 세션도 job 도 전무해야 "볼 것이 없는 화면"이다 — job 만 있어도 실데이터가 있는 것이다 */
  const empty = hasNoSignal(data);

  /* ⚠️ 재조회 실패 표기는 **분기 위**에 둔다. 분기마다 세우면 형제가 하나씩 빠지고, 하필
   * 빠지는 쪽이 부재를 가장 세게 단정하는 분기다 — "세션이 없다 · 오류가 아니라 관측 결과다"를
   * 조회가 실패한 상태로 쓰면 장애가 실측 0으로 읽힌다. */
  return (
    <>
      {isError && (
        <p className="t-xs m-0" style={{ color: 'var(--warn)' }}>
          실시간 상태 재조회에 실패했습니다 — 직전 실측을 유지합니다.
        </p>
      )}
      {empty ? (
        <div className="flex flex-col gap-4">
          <EmptyRealNotice>
            이 날짜({data.date})의 세션이 없습니다 — 1분 파이프라인이 계획되지 않았다는 사실이다(비거래일
            또는 미가동). 오류가 아니라 관측 결과다. 뉴스 추출 job 도 0건이다.
          </EmptyRealNotice>
        </div>
      ) : (
      <MinuteBody
      data={data}
      date={date}
      setDate={setDate}
      dataset={params.get('dataset') ?? ''}
      sourceGroup={sourceGroup}
      setDataset={setDataset}
      updatedAt={dataUpdatedAt}
      />
      )}
    </>
  );
}

/**
 * 화면에 세울 데이터셋 탭. 세션이 있는 dataset 전부 + 뉴스는 **항상** 세운다 —
 * 추출 job 이 날짜 축이라 세션이 없는 날에도 볼 사실이 있고, 탭이 사라지면 "뉴스 세션이
 * 안 섰다"는 사실 자체가 화면에서 없어진다.
 */
function datasetTabs(data: MinuteStatus, requested: string): { id: string; sessions: MinuteSession[] }[] {
  const requestedKnown = REALTIME_DATASETS.has(requested) ? [requested] : [];
  const ids = [...new Set([...data.sessions.map((s) => s.dataset), 'news_minute', ...requestedKnown])];
  ids.sort((a, b) => {
    const ai = DATASET_ORDER.indexOf(a);
    const bi = DATASET_ORDER.indexOf(b);
    return (ai < 0 ? DATASET_ORDER.length : ai) - (bi < 0 ? DATASET_ORDER.length : bi) || a.localeCompare(b);
  });
  return ids.map((id) => ({ id, sessions: data.sessions.filter((s) => s.dataset === id) }));
}

function MinuteBody({
  data,
  date,
  setDate,
  dataset,
  sourceGroup,
  setDataset,
  updatedAt,
  mock = false,
}: {
  data: MinuteStatus;
  date: string;
  setDate: (v: string) => void;
  dataset: string;
  sourceGroup?: string;
  setDataset: (v: string) => void;
  updatedAt: number;
  mock?: boolean;
}) {
  const tabs = datasetTabs(data, dataset);
  const historical = !isCurrentKstDate(data.date);
  /* 어휘 밖 dataset 은 첫 탭으로 정규화한다 — 빈 화면을 내면 잘못된 링크가 "세션 없음"으로
   * 위장된다(그 날짜에 진짜로 세션이 없는 것과 구분이 사라진다). */
  const current = tabs.find((t) => t.id === dataset) ?? tabs[0];
  const currentSessions = sessionsForSourceGroup(current.sessions, sourceGroup);
  const kind = datasetKind(current.id);

  return (
    <div className="flex flex-col gap-4">
      {/* ── 공통 영역 — 거래일과 기준 시각까지. 데이터셋을 합친 상태는 만들지 않는다 ── */}
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
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          세션 시간 <b>관측 불가</b>
          <InfoPopover
            label="세션 시간"
            text="이 응답은 세션 개시·종료 시각을 주지 않는다. 기대 창 수로 길이를 역산하면 실제 개시 시각과 다른 축을 지어내는 것이라 하지 않는다."
          />
        </span>
        <span style={{ flex: 1 }} />
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          받은 시각 {clock(new Date(updatedAt).toISOString())} · 60초마다 자동 갱신
          <InfoPopover
            label="상태 기준"
            title="상태 기준"
            text={
              '받은 시각은 이 브라우저가 응답을 받은 시각이다 — 무증거 · lease 만료 · claimed 만료의 판정 시계는 서버(DB now())이고 화면이 다시 계산하지 않는다.\n\n' +
              '장중은 실행 시간대이지 판정 단위가 아니다 — 데이터셋마다 기준이 달라 합친 상태를 만들지 않는다.\n' +
              '실행 축 — 세션이 살아 있는가(phase + lease). 종료 국면의 lease 만료는 정상이다.\n' +
              '창·poll 축 — 1분 단위 결과 상태. 무증거(기한 지났는데 결과 없음)와 결과가 빈 정상(가격=거래 없음, 뉴스=신규 기사 없음)은 다른 사실이다.\n' +
              'job 축 — 논리 job 큐. grain 이 달라 창 집계와 합치지 않는다.\n' +
              'MISSING 은 EOD QC 판정이라 장중에는 매겨지지 않는다.'
            }
          />
        </span>
      </div>

      {/* ── 실행 중인 배치 — 수집 세션과 다른 축이라 합산하지 않는다 ── */}
      <BatchRunning historical={historical} />

      {/* ── 활성 수집 세션 — 데이터셋 선택으로 본문이 통째로 바뀐다 ── */}
      <p className="t-sm m-0" style={{ fontWeight: 600 }}>
        활성 수집 세션
        {historical && (
          <span className="t-xs" style={{ color: 'var(--fg-3)', fontWeight: 400 }}>
            {' '}
            — 지난 날짜({data.date})를 보고 있어 실행 중인 배치는 표시하지 않습니다
          </span>
        )}
      </p>
      <div className="mn-tabs" role="group" aria-label="데이터셋 선택">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            className="mn-tab"
            aria-pressed={t.id === current.id}
            onClick={() => setDataset(t.id)}
          >
            <span className="mn-tab-name">{DATASET_LABEL[t.id] ?? t.id}</span>
            <span className="mn-tab-sub mono">
              {t.id}
              {t.sessions.length === 0 ? ' · 세션 없음' : t.sessions.length > 1 ? ` · 세션 ${t.sessions.length}` : ''}
            </span>
          </button>
        ))}
      </div>

      {currentSessions.length === 0 ? (
        <div className="card card-pad">
          <p className="t-sm m-0">
            이 날짜에 {DATASET_LABEL[current.id] ?? current.id}
            {sourceGroup ? ` / ${sourceGroup}` : ''} 세션이 없습니다.
          </p>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
            {sourceGroup
              ? '이 벤더 세션이 없다는 사실입니다 — 데이터셋 전체의 계획 여부를 뜻하지 않습니다.'
              : '이 데이터셋이 계획되지 않았다는 사실입니다(비거래일 · 미가동 · 레인 미편입).'}{' '}
            다른 데이터셋의 상태로 이 데이터셋을 대신 판정하지 않습니다.
          </p>
        </div>
      ) : (
        currentSessions.map((s) =>
          datasetKind(s.dataset) === 'news' ? (
            <NewsSessionCard key={s.sessionId} session={s} date={data.date} mock={mock} />
          ) : datasetKind(s.dataset) === 'price' ? (
            <PriceSessionCard key={s.sessionId} session={s} date={data.date} mock={mock} />
          ) : (
            <GenericSessionCard key={s.sessionId} session={s} date={data.date} mock={mock} />
          ),
        )
      )}

      {/* 추출 job 은 뉴스 데이터셋의 사실이다 — 세션 유무와 무관하게 날짜 축으로 존재한다 */}
      {kind === 'news' && <NewsJobsCard jobs={data.newsJobs} date={data.date} mock={mock} />}

      {/* 못 그리는 것을 조용히 넘기지 않는다 — 선택한 데이터셋의 부채만 남긴다 */}
      <ApiGapList gaps={kind === 'news' ? [...NEWS_API_GAPS, ...MINUTE_API_GAPS] : MINUTE_API_GAPS} />
    </div>
  );
}
