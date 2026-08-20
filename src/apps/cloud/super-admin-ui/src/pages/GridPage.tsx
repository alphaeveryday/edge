/* 실행 이력 — 데이터셋별 일 단위 실행 요약 (ALPHA-594 → ALPHA-738).
 *
 * 답하는 질문: "최근 며칠 동안 각 데이터셋의 예정된 실행·수집이 정상으로 귀결됐는가?"
 *
 * 역할 분리:
 *   실행 이력(여기) — 여러 **날짜**를 비교하는 일 단위 요약. 박스 하나 = 데이터셋 × 날짜.
 *   실시간 세션(/minute) — 특정 날짜의 분·poll 단위 상태. 여기서 복제하지 않고 지목해 보낸다.
 *   실행 원장 상세(/sources) — 개별 실행의 시도·이슈·산출물.
 *
 * 행은 **데이터셋**이 직접 선다. 시장·뉴스·일배치·실시간은 **필터와 배지**이지 상태를 갖는
 * 부모 행이 아니다 — 그 층위는 실제 제어 단위가 아니라서 성공/장애를 매길 원장 근거가 없다.
 *
 * 실행 인스턴스는 화면에서만 union 이다(DB 통합 아님):
 *   배치 실행    — ops 원장의 런×작업  → /sources 실행 상세
 *   실시간 세션  — minute_ingestion_session → /minute?date=&dataset= 세션 상세
 * 1분 창 390개를 최상위 런으로 세우지 않는다 — 실시간의 상위 단위는 데이터셋 × 세션 날짜다.
 *
 * 데이터셋 축은 화면 쪽 카탈로그다 — 격자 API 가 dataset 을 주지 않는다(datasetCatalog 참고).
 * 상태·기대 실행 수는 원장 값에서만 센다(dailyRollup 참고) — 주기로 숫자를 지어내지 않는다.
 */
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { PageSkeleton, StatusBadge } from 'ui-kit';
import type { BadgeTone } from 'ui-kit';
import type { MinuteStatus, SourceGrid } from '../domains/sources';
import { useMinuteStatus, useSourceGrid } from '../domains/sources/hooks';
import type { IntradayAnalysisTrendDto } from '../domains/console';
import { useIntradayAnalysisTrend } from '../domains/console/hooks';
import { intradayOutcome, kstDateAt } from '../domains/console/intradayAnalysisTrend';
import type { IntradayOutcomeKind } from '../domains/console/intradayAnalysisTrend';
import {
  ALL_DATASETS,
  CATALOG_SOURCE,
  DATASET_DOMAINS,
  DATASET_KINDS,
  kindOf,
} from '../domains/sources/datasetCatalog';
import type { DatasetDomain, DatasetEntry, DatasetKindLabel } from '../domains/sources/datasetCatalog';
import { datesOf, realtimeDayState, realtimeSessionState, rollup } from '../domains/sources/dailyRollup';
import type { DayExecution, DayRollup, DayState } from '../domains/sources/dailyRollup';
import { MOCK_GRID } from '../mock/preview';
import { EmptyRealNotice, MockChip, MockPreview } from './_shared/MockPreview';
import { InfoPopover } from './_shared/InfoPopover';
import { minuteSessionHref, RUN_DETAIL_UNAVAILABLE } from './ops/investigation';
import { LoadError } from './_shared/LoadError';
import {
  minuteDetailData,
  resolveMinuteDetail,
  shouldFetchMinuteDetail,
} from '../domains/sources/minuteHistory';
import type { MinuteDetailState } from '../domains/sources/minuteHistory';
import '../styles/grid.css';

/* 상태 → 박스 모양. 색 하나에만 기대지 않도록 테두리·사선·빈 칸을 함께 쓴다.
 * 값은 기존 격자 인코딩을 물려받는다(초록 성공 · 주황 주의 · 빨강 장애 · 파란 테두리 실행 중 · 사선 스킵). */
const STATE_CLASS: Record<DayState, string> = {
  정상: 'gd-s-ok',
  주의: 'gd-s-warn',
  장애: 'gd-s-bad',
  '실행 중': 'gd-s-run',
  대기: 'gd-s-wait',
  '계획 스킵': 'gd-s-skip',
  '계획 없음': 'gd-s-none',
  '상태 미제공': 'gd-s-nostate',
};
const STATE_ORDER: DayState[] = [
  '정상',
  '주의',
  '장애',
  '실행 중',
  '대기',
  '계획 스킵',
  '계획 없음',
  '상태 미제공',
];
const STATE_TONE: Record<DayState, BadgeTone> = {
  정상: 'active',
  주의: 'warn',
  장애: 'blocked',
  '실행 중': 'env',
  대기: 'neutral',
  '계획 스킵': 'gated',
  '계획 없음': 'neutral',
  '상태 미제공': 'neutral',
};

/* 원장 stage 어휘 → 표시명. 모르는 stage 는 원문 그대로 둔다 */
const STAGE_LABEL: Record<string, string> = { raw: '수집', normalize: '정제', feature: '적재' };

const OUTCOME_LABEL: Record<string, string> = {
  FULFILLED: '성공',
  FAILED: '실패',
  BLOCKED: '선행 미충족',
  MISSED: '무증거',
  PENDING: '대기',
};

const STATUS_TIP = [
  '박스 하나 = 데이터셋 × 날짜. 그날의 **실행 인스턴스** 상태를 전부 접은 결과다.',
  '실행 인스턴스는 runKey 하나다 — 한 런에 작업이 3개여도 실행은 1회로 센다.',
  '',
  '정상 — 기한이 지난 기대 실행이 모두 정상 귀결됐다',
  '주의 — 불완전·무효·유실 등 확인이 필요하다',
  '장애 — 실패 또는 기한이 지난 무증거가 있다',
  '실행 중 — 아직 끝나지 않은 것이 남았다',
  '대기 — 계획은 있고 아직 기한 전이다. 기한 전 대기를 실패로 보지 않는다',
  '계획 스킵 — 계획 단계에서 빠졌다(비거래일 등). 안 한 게 아니라 할 일이 아니었다',
  '계획 없음 — 그 날짜에 이 데이터셋의 계획 행 자체가 없다. 대기(계획은 있다)와 다른 사실이라',
  '  박스 모양을 갈라 둔다 — 합치면 계획 결손 후보가 정상으로 보인다',
  '',
  '빈 데이터와 무증거는 합치지 않는다.',
  '  빈 데이터(VALID_EMPTY) — 돌았고 그 날 데이터가 없었다는 증거가 남았다. 정상이다.',
  '  무증거(MISSED) — 기한이 지났는데 실행·결과 증거가 없다. 장애다.',
  '',
  '상태 미제공 — API 가 그 날짜의 판정 값을 주지 않았다. 계획 없음(계획 행이 없다)과 다른',
  '  사실이라 합치지 않는다. 실시간 데이터셋은 최근 7일 요약 엔드포인트가 없어 여기 해당한다.',
  '',
  '기대 실행 수는 주기에서 지어내지 않고 계획(plan_status=DUE)이 있던 실행 인스턴스를 센다 —',
  '작업 수가 아니다. 그래서 일배치와 실시간 데이터셋에 같은 기대 실행 수가 적용되지 않는다.',
  '',
  '유형(일배치·실시간)과 도메인(시장·뉴스)은 필터·배지다 — 그 층위는 실제 제어 단위가',
  '아니라서 성공/장애를 매길 원장 근거가 없다. 그래서 그룹 롤업 행을 두지 않는다.',
].join('\n');

/** 박스 — 격자와 범례가 이 컴포넌트 하나를 공유한다(두 곳에 모양을 복제하면 범례가 거짓말한다) */
function StateBox({ state }: { state: DayState }) {
  return <span className={`gd-cell ${STATE_CLASS[state]}`} />;
}
function GridLegend() {
  return (
    <div className="gd-legend">
      {STATE_ORDER.map((s) => (
        <span key={s} className="gd-legend-item">
          <StateBox state={s} />
          {s}
        </span>
      ))}
      <InfoPopover label="상태 기준" title="상태 기준" text={STATUS_TIP} />
    </div>
  );
}

const mmdd = (d: string) => d.slice(5);

/** 필터 버튼 묶음 — 유형·도메인 두 축이 같은 모양을 쓴다(축마다 다른 조작을 만들지 않는다) */
function FilterRow<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T | 'all';
  options: T[];
  onChange: (v: T | 'all') => void;
}) {
  const all: (T | 'all')[] = ['all', ...options];
  return (
    <span className="gd-filter" role="group" aria-label={`${label} 필터`}>
      <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
        {label}
      </span>
      {all.map((o) => (
        <button
          key={o}
          type="button"
          className="gd-filterbtn"
          aria-pressed={value === o}
          onClick={() => onChange(o)}
        >
          {o === 'all' ? '전체' : o}
        </button>
      ))}
    </span>
  );
}

export function GridPage() {
  const { data: grid, isPending, isError, error } = useSourceGrid();
  /* 실시간 레인의 하루치 세션 — 격자 원장에 없는 행을 세션 원장이 답할 수 있는 만큼만 채운다.
   * 실패해도 격자는 그린다(세션이 없으면 예전처럼 상태 미제공). */
  const { data: minute, isError: minuteError, dataUpdatedAt: minuteUpdatedAt } = useMinuteStatus();
  const intraday = useIntradayAnalysisTrend(undefined, 30);
  const [selectedOutcomeDate, setSelectedOutcomeDate] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedOutcomeDate) return;
    window.requestAnimationFrame(() => {
      const detail = document.getElementById('gd-outcome-detail');
      detail?.focus({ preventScroll: true });
      const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      detail?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'nearest' });
    });
  }, [selectedOutcomeDate]);

  useEffect(() => {
    if (
      selectedOutcomeDate &&
      intraday.data &&
      !intraday.data.points.some((point) => point.date === selectedOutcomeDate)
    ) {
      setSelectedOutcomeDate(null);
    }
  }, [intraday.data, selectedOutcomeDate]);

  const closeOutcome = () => {
    const trigger = selectedOutcomeDate;
    setSelectedOutcomeDate(null);
    if (trigger) {
      window.requestAnimationFrame(() => document.getElementById(`gd-outcome-day-${trigger}`)?.focus());
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <IntradayOutcomeStrip
        trend={intraday.data}
        pending={intraday.isPending}
        failed={intraday.isError}
        selectedDate={selectedOutcomeDate ?? undefined}
        onSelectDate={(date) => setSelectedOutcomeDate(selectedOutcomeDate === date ? null : date)}
      />
      {selectedOutcomeDate && (
        <IntradayOutcomeDetail
          date={selectedOutcomeDate}
          trend={intraday.data}
          pending={intraday.isPending}
          failed={intraday.isError}
          onClose={closeOutcome}
        />
      )}
      {isError ? (
        <LoadError error={error} />
      ) : isPending ? (
        <PageSkeleton rows={6} />
      ) : (
        <GridBody
          grid={grid}
          minute={minute}
          minuteError={minuteError}
          minuteUpdatedAt={minuteUpdatedAt}
        />
      )}
    </div>
  );
}

interface Selection {
  dataset: DatasetEntry;
  date: string;
}

/**
 * 실시간 레인의 그날 상태 — 격자 원장(ops_expected_task)에 이 레인의 행이 없으므로,
 * **세션 원장이 그날의 사실을 줄 때만** 그 판정을 그대로 편다.
 *
 * 지키는 선:
 *   · 세션 응답은 하루치다 — 채워지는 날짜는 응답이 말한 그 하루뿐이고 나머지는 여전히
 *     `상태 미제공` 이다. 없는 날짜의 판정을 옆 날짜에서 복사하지 않는다.
 *   · **창 카운트로 그날의 귀결(정상·주의)을 만들지 않는다.** 진행 중인 세션에 완결 판정을
 *     붙이는 셈이고, 커버리지 분모도 하한이라(minuteView 참고) 근거가 못 된다.
 *   · 실행체 생존만 편다 — `liveness` 는 phase 와 leaseExpired(서버 DB 시계 판정)의 파생이라
 *     화면이 새로 만드는 판정이 아니다. 종료 국면·lease 부재는 그날의 귀결을 답하지 않으므로
 *     null 로 두고 `상태 미제공` 에 맡긴다.
 */
function sessionState(
  d: DatasetEntry,
  date: string,
  minute?: MinuteStatus,
): { state: DayState; basis: string } | null {
  if (!d.sessionDataset || !minute || minute.date !== date) return null;
  return realtimeDayState(d.sessionDataset, date, minute);
}

function GridBody({
  grid,
  minute,
  minuteError = false,
  minuteUpdatedAt = 0,
  mock = false,
}: {
  grid: SourceGrid;
  minute?: MinuteStatus;
  minuteError?: boolean;
  minuteUpdatedAt?: number;
  mock?: boolean;
}) {
  const [kindFilter, setKindFilter] = useState<DatasetKindLabel | 'all'>('all');
  const [domainFilter, setDomainFilter] = useState<DatasetDomain | 'all'>('all');
  const [selected, setSelected] = useState<Selection | null>(null);
  const selectedTarget = selected ? `dataset:${selected.dataset.id}:${selected.date}` : null;
  const selectedMinuteDate = selected?.dataset.sessionDataset ? selected.date : undefined;
  const fetchSelectedMinute = shouldFetchMinuteDetail(selectedMinuteDate, minute?.date, mock);
  const selectedMinuteQuery = useMinuteStatus(selectedMinuteDate, fetchSelectedMinute);
  const selectedMinuteData = minuteDetailData(
    selectedMinuteDate,
    minute,
    minuteUpdatedAt,
    selectedMinuteQuery.data,
    selectedMinuteQuery.dataUpdatedAt,
  );
  const selectedMinuteDetail = selectedMinuteDate && selected?.dataset.sessionDataset && !mock
    ? resolveMinuteDetail(
        selectedMinuteDate,
        selected.dataset.sessionDataset,
        selectedMinuteData,
        selectedMinuteQuery.isPending,
        fetchSelectedMinute ? selectedMinuteQuery.isError : minuteError,
      )
    : undefined;

  /* 상세 카드는 격자 아래에 붙는다 — 데이터셋·날짜가 늘면 접힌 화면 밖이라 박스를 눌러도
   * 아무 일이 없는 것처럼 보인다(10일 × 10행에서 top 815px 을 쟀다).
   * `block: 'nearest'` 로 최소한만 굴린다 — 카드를 화면 맨 위로 올리면 어느 박스를 눌렀는지
   * 시야에서 잃는다. 모션 축소 설정은 존중한다. */
  useEffect(() => {
    if (!selectedTarget) return;
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    document
      .getElementById('gd-detail')
      ?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'nearest' });
  }, [selectedTarget]);

  /* 필터는 **행**에만 건다 — 슬롯을 걸러 날짜 축까지 바뀌면 필터를 바꿀 때마다 열이 움직인다 */
  const dates = useMemo(() => {
    const batchDates = datesOf(grid.slots);
    return minute && !batchDates.includes(minute.date)
      ? [...batchDates, minute.date].sort()
      : batchDates;
  }, [grid.slots, minute]);
  const rolled = useMemo(() => rollup(grid.slots), [grid.slots]);
  const at = (datasetId: string, date: string) => rolled.get(`${datasetId}|${date}`);

  useEffect(() => {
    if (selected && !dates.includes(selected.date)) setSelected(null);
  }, [dates, selected]);

  /* 이 창에서 셀이 하나도 없는 배치 데이터셋은 감춘다 — 통째로 빈 행은 소음이다.
   * 실시간 데이터셋은 이 격자에 셀이 아예 없는 게 정상이라 그 규칙에서 뺀다. */
  const rows = ALL_DATASETS.filter((d) => {
    if (kindFilter !== 'all' && kindOf(d) !== kindFilter) return false;
    if (domainFilter !== 'all' && d.domain !== domainFilter) return false;
    return !d.inOpsGrid || dates.some((date) => at(d.id, date) !== undefined);
  });

  /* 실측 결과 스트립은 이 분기 밖 GridPage에 남고, 목 격자는 아래에서 따로 검수한다. */
  if (grid.slots.length === 0 && !minute && !mock) {
    return (
      <div className="flex flex-col gap-4">
        <EmptyRealNotice>최근 {grid.days}일 안에 기록된 파이프라인 실행이 없습니다.</EmptyRealNotice>
        <MockPreview><GridBody grid={MOCK_GRID} mock /></MockPreview>
      </div>
    );
  }

  /**
   * 셀 상태 — **데이터 출처를 상태로 만들지 않는다.** 배치든 실시간이든 같은 어휘를 쓰고,
   * 판정 값이 없을 때만 `상태 미제공` 이다. 실시간은 최근 7일 요약 API 가 없어, 세션 응답이 답하는
   * 하루(sessionState)를 빼면 전부 여기 해당한다(목 미리보기에서는 목표 구조를 볼 수 있게 목 판정).
   */
  const boxState = (d: DatasetEntry, date: string): DayState => {
    const r = at(d.id, date);
    if (r) return r.state;
    return sessionState(d, date, minute)?.state ?? (d.inOpsGrid ? '계획 없음' : '상태 미제공');
  };

  return (
    <div className="flex flex-col gap-4">
      {minuteError && (
        <div className="card card-pad t-xs" style={{ color: 'var(--fg-3)' }}>
          실시간 세션 축을 갱신하지 못했습니다 — {minute ? '직전 실측을 유지합니다.' : '상태 미제공으로 표시합니다.'}
        </div>
      )}
      <div className="card">
        <div className="card-head">
          <span className="t-label">파이프라인 실행 이력 {mock && <MockChip />}</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            최근 {grid.days}일 · 데이터셋별 일 단위
          </span>
        </div>

        <div className="card-pad" style={{ paddingTop: 0 }}>
          <div className="gd-filters">
            <FilterRow
              label="유형"
              value={kindFilter}
              options={DATASET_KINDS}
              onChange={(v) => {
                setKindFilter(v);
                setSelected(null);
              }}
            />
            <FilterRow
              label="도메인"
              value={domainFilter}
              options={DATASET_DOMAINS}
              onChange={(v) => {
                setDomainFilter(v);
                setSelected(null);
              }}
            />
          </div>
          <GridLegend />
          <div className="gd-hint">
            <span>
              <b>박스</b> 선택 → 그 데이터셋·날짜의 실행 목록
            </span>
            <span>
              실행 목록에서 <b>실시간 세션</b>은 세션 상세로 갑니다 — <b>배치 실행</b>은 여기까지입니다
            </span>
            <span style={{ marginLeft: 'auto' }}>
              분·poll 단위 상태는 <Link to="/minute">현재 실행</Link>이 답합니다
            </span>
          </div>
        </div>

        {dates.length === 0 ? (
          <div className="card-pad" style={{ paddingTop: 0 }}>
            <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
              최근 {grid.days}일 안에 기록된 파이프라인 실행이 없습니다.
            </p>
          </div>
        ) : (
          <div className="gd-scroll">
            <table className="gd-table">
              <thead>
                <tr>
                  <th className="gd-rowhead" />
                  {dates.map((d) => (
                    <th key={d} className="gd-datehead" scope="col">
                      {mmdd(d)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((d) => (
                  <tr key={d.id}>
                    <th className="gd-rowhead" scope="row">
                      <span className="gd-rowname">{d.label}</span>
                      {/* 유형·도메인은 배지다 — 상태를 갖는 부모 행으로 세우지 않는다 */}
                      <span className="gd-rowmeta">
                        <span className="chip">{kindOf(d)}</span>
                        <span className="chip">{d.domain}</span>
                        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
                          {d.cadence.label}
                        </span>
                      </span>
                    </th>
                    {dates.map((date) => {
                      const r = at(d.id, date);
                      const st = boxState(d, date);
                      const sel = selected?.dataset.id === d.id && selected?.date === date;
                      return (
                        <td key={date} className="gd-box">
                          <button
                            type="button"
                            className={'gd-cellbtn' + (sel ? ' gd-selected' : '')}
                            aria-pressed={sel}
                            title={boxTip(d, date, r, sessionState(d, date, minute))}
                            aria-label={`${d.label} ${date} ${st} — 그날 실행 목록 보기`}
                            onClick={() => {
                              setSelected(sel ? null : { dataset: d, date });
                            }}
                          >
                            <StateBox state={st} />
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="card-pad" style={{ paddingTop: 0 }}>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            행 축(데이터셋 · 유형 · 도메인 · 수집 주기)은 <b>{CATALOG_SOURCE}</b>입니다 — 격자 응답이
            데이터셋을 주지 않아 화면이 작업을 데이터셋으로 묶습니다. 상태와 기대 실행 수는 원장
            값에서만 셉니다. 실시간 데이터셋의 판정 출처는 <b>minute_ingestion_session/window</b>이고,
            그 원장에는 최근 7일 일별 요약 엔드포인트가 없습니다 — 세션 응답이 답하는 <b>하루</b>만
            그 세션의 실행체 생존(실행 중 · 장애)을 펴고, 나머지 날짜는 <b>상태 미제공</b>으로 둡니다.
            창 카운트로 그날의 귀결을 만들지 않습니다.
          </p>
        </div>
      </div>

      {selected && (
            <DayDetail
              sel={{ ...selected, rollup: at(selected.dataset.id, selected.date) }}
              minuteDetail={selectedMinuteDetail}
              mock={mock}
              onClose={() => setSelected(null)}
            />
      )}
    </div>
  );
}

const OUTCOME_TONE: Record<IntradayOutcomeKind, BadgeTone> = {
  collecting: 'env', none: 'neutral', complete: 'active', partial: 'warn', missing: 'blocked',
};

function IntradayOutcomeStrip({ trend, pending, failed, selectedDate, onSelectDate }: {
  trend?: IntradayAnalysisTrendDto;
  pending: boolean;
  failed: boolean;
  selectedDate?: string;
  onSelectDate: (date: string) => void;
}) {
  const current = trend ? kstDateAt(trend.asOf) : '';
  return (
    <section className="card" aria-labelledby="gd-outcome-title">
      <div className="card-head gd-outcome-head">
        <span className="t-label" id="gd-outcome-title">장중 분석 귀결</span>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>최근 30일 · 수집 상태와 별도인 분석 결과 축</span>
      </div>
      <div className="card-pad" style={{ paddingTop: 0 }}>
        {failed && trend && (
          <p className="t-xs" style={{ color: 'var(--warn)', marginTop: 0 }}>
            분석 귀결 재조회에 실패해 직전 실측을 유지합니다.
          </p>
        )}
        {!trend ? (
          <p className="t-sm m-0" role="status" style={{ color: 'var(--fg-3)' }}>
            {pending ? '장중 분석 귀결을 불러오는 중입니다.' : '장중 분석 귀결 상태 미제공 — 조회 실패를 0건으로 표시하지 않습니다.'}
          </p>
        ) : (
          <div className="gd-outcome-scroll" aria-label="최근 30일 장중 분석 귀결">
            {[...trend.points].reverse().map((point) => {
              const outcome = intradayOutcome(point, current);
              return (
                <button
                  key={point.date}
                  id={`gd-outcome-day-${point.date}`}
                  type="button"
                  className={`gd-outcome-day gd-outcome-${outcome.kind}${selectedDate === point.date ? ' gd-outcome-selected' : ''}`}
                  aria-label={`${point.date} ${outcome.label}, 결과 ${point.results}/${point.triggers}, 게시 ${point.published}`}
                  aria-pressed={selectedDate === point.date}
                  onClick={() => onSelectDate(point.date)}
                >
                  <span className="gd-outcome-date">{mmdd(point.date)}</span>
                  <span className="gd-outcome-mark" aria-hidden="true" />
                  <span className="gd-outcome-label">{outcome.label}</span>
                  <span className="gd-outcome-ratio">결과 {point.results}/{point.triggers}</span>
                </button>
              );
            })}
          </div>
        )}
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 8 }}>
          판정은 FIRE 트리거의 결과 도달 여부만 봅니다. 현재일은 완료 여부를 확정하지 않으며,
          데이터셋 수집 상태를 이 색으로 덮어쓰지 않습니다.
        </p>
      </div>
    </section>
  );
}

function IntradayOutcomeDetail({ date, trend, pending, failed, onClose }: {
  date: string;
  trend?: IntradayAnalysisTrendDto;
  pending: boolean;
  failed: boolean;
  onClose?: () => void;
}) {
  const point = trend?.points.find((candidate) => candidate.date === date);
  const outcome = point && trend ? intradayOutcome(point, kstDateAt(trend.asOf)) : null;
  return (
    <section className="card" id="gd-outcome-detail" tabIndex={-1} aria-labelledby="gd-outcome-detail-title">
      <div className="card-head gd-outcome-head">
        <span className="t-label" id="gd-outcome-detail-title">장중 분석 귀결 · {date}</span>
        {outcome ? <StatusBadge tone={OUTCOME_TONE[outcome.kind]}>{outcome.label}</StatusBadge> : <StatusBadge tone="neutral">상태 미제공</StatusBadge>}
        <span className="t-xs" style={{ color: 'var(--fg-3)', marginLeft: 'auto' }}>
          분석 결과 축 · 아래 데이터셋 수집 상세와 별도
        </span>
        {onClose && <button type="button" className="btn btn-sm" onClick={onClose}>닫기</button>}
      </div>
      <div className="card-pad">
        {point ? (
          <>
            {failed && <p className="t-xs" style={{ color: 'var(--warn)', marginTop: 0 }}>재조회에 실패해 이 날짜의 직전 실측을 유지합니다.</p>}
            <div className="gd-outcome-flow" aria-label={`장중 분석 흐름: 트리거 ${point.triggers}, 관측 ${point.observations}, 실행 ${point.runs}, 결과 ${point.results}, 게시 ${point.published}`}>
              <OutcomeStage label="트리거" value={point.triggers} denominator={point.triggers} />
              <OutcomeStage label="관측" value={point.observations} denominator={point.triggers} />
              <OutcomeStage label="실행" value={point.runs} denominator={point.triggers} />
              <OutcomeStage label="결과" value={point.results} denominator={point.triggers} />
              <OutcomeStage label="게시" value={point.published} denominator={point.triggers} last />
            </div>
            <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 10 }}>
              최신 실행 상태 중 진행 {point.activeRuns} · 실패 {point.failedRuns} · DB 기준시각{' '}
              {new Date(trend!.asOf).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', hour12: false })}
            </p>
          </>
        ) : (
          <p className="t-sm m-0" role="status" style={{ color: 'var(--fg-3)' }}>
            {pending ? '선택 날짜의 분석 귀결을 불러오는 중입니다.' : '선택 날짜의 분석 귀결 상태 미제공 — 조회 실패·응답 부재를 발화 0건으로 합성하지 않습니다.'}
          </p>
        )}
      </div>
    </section>
  );
}

function OutcomeStage({ label, value, denominator, last = false }: {
  label: string; value: number; denominator: number; last?: boolean;
}) {
  return (
    <>
      <span className="gd-outcome-stage"><span>{label}</span><b>{value}/{denominator}</b></span>
      {!last && <span className="gd-outcome-arrow" aria-hidden="true">→</span>}
    </>
  );
}

function boxTip(
  d: DatasetEntry,
  date: string,
  r?: DayRollup,
  live?: { state: DayState; basis: string } | null,
): string {
  if (!d.inOpsGrid) {
    if (live) {
      return [
        `${d.label} · ${date} · ${live.state}`,
        `판정 출처: ${d.cadence.kind === 'intradayWindows' ? d.cadence.ledger : '다른 원장'} 세션`,
        live.basis,
        '이 날짜의 일별 요약은 여전히 없습니다 — 세션 생존만 편 것이고, 분·poll 단위는 세션 상세가 답합니다',
      ].join('\n');
    }
    return [
      `${d.label} · ${date} · 상태 미제공`,
      `판정 출처: ${d.cadence.kind === 'intradayWindows' ? d.cadence.ledger : '다른 원장'}`,
      '그 원장에 최근 7일 일별 요약 엔드포인트가 없어 이 날짜의 판정 값을 받지 못했습니다',
      '없는 상태를 지어내지 않습니다 — 선택하면 그날의 세션 상세로 갑니다',
    ].join('\n');
  }
  if (!r) return `${d.label} · ${date} · 계획 없음\n이 날짜에 계획 행이 없습니다`;
  const c = r.counts;
  return [
    `${d.label} · ${date} · ${r.state}`,
    `주기 ${d.cadence.label}`,
    `기대 실행 ${r.expected}`,
    `성공 ${c.fulfilled}`,
    c.emptyEvidence ? `빈 데이터 증거 ${c.emptyEvidence}` : '',
    c.failed ? `실패 ${c.failed}` : '',
    c.incomplete ? `불완전 ${c.incomplete}` : '',
    c.invalid ? `무효 ${c.invalid}` : '',
    c.noEvidence ? `무증거 ${c.noEvidence}` : '',
    c.pending ? `판정 대기 ${c.pending}` : '',
    c.running ? `실행 중 ${c.running}` : '',
    c.skipped ? `계획 스킵 ${c.skipped}` : '',
  ]
    .filter(Boolean)
    .join('\n');
}

/**
 * 박스 선택 → 그날의 **실행 인스턴스 목록**. 작업은 실행을 펼쳐야 나온다.
 *
 * 기본은 접힘이되 **문제 있는 실행만 펼친다** — 정상 실행까지 펼치면 하루 10회 × 작업 3개가
 * 다시 30행 평탄화가 된다. 일별 배지만 두고 어느 실행이 장애인지 못 찾는 상태로 두지 않는다.
 *
 * 정식 조사 순서는 실행 이력 → 실행 목록 → 실행 상세 → 작업 상세 → 원장 근거지만,
 * **실행 상세로 가는 진입점은 지금 없다** — 그 화면이 최근 조회일 하루만 읽어 이 격자의
 * 임의 날짜 런을 해소하지 못한다(`RUN_DETAIL_UNAVAILABLE`). 여기서 답할 수 있는 데까지가
 * 이 표다.
 *
 * ⚠️ 런 kind(정규·수동·백필)는 격자 응답에 없다(decisions.md §3-4 계측 부채) — 여기서
 * runKey 모양으로 추측하지 않고 `배치 실행`까지만 단언한다.
 */
function DayDetail({
  sel,
  minuteDetail,
  mock,
  onClose,
}: {
  sel: Selection & { rollup?: DayRollup };
  minuteDetail?: MinuteDetailState;
  mock: boolean;
  onClose: () => void;
}) {
  const { dataset: d, date, rollup: r } = sel;

  /* 실시간 데이터셋 — 이 격자에는 그날의 판정이 없다. 세션 한 건을 실행 인스턴스로 세우고 보낸다.
   * 1분 창 390개를 최상위 실행 390개로 펼치지 않는다(상위 단위는 데이터셋 × 세션 날짜). */
  if (d.sessionDataset) {
    /* 링크는 손으로 조립하지 않는다 — 인코딩 계약이 `minuteSessionHref` 한 자리에 있고,
     * 여기서 다시 적으면 구분자 든 벤더가 생기는 날 이 링크만 조용히 깨져 도착 화면이
     * "이 벤더 세션이 없습니다"라는 **거짓 부재**를 단언한다. */
    const href = minuteSessionHref(date, d.sessionDataset);
    const sessions = minuteDetail?.kind === 'ready' ? minuteDetail.sessions : [];
    const live = minuteDetail?.kind === 'ready'
      ? sessionState(d, date, minuteDetail.minute)
      : null;
    const detailMessage = minuteDetail?.kind === 'loading'
      ? '선택 날짜의 장중 세션을 불러오는 중입니다.'
      : minuteDetail?.kind === 'error'
        ? '선택 날짜의 장중 세션을 조회하지 못했습니다. 세션 부재로 판단하지 않습니다.'
        : minuteDetail?.kind === 'stale'
          ? '다른 날짜의 응답은 표시하지 않습니다. 선택 날짜 응답을 기다리고 있습니다.'
          : null;
    return (
      <div className="card" id="gd-detail">
        <div className="card-head">
          <span className="t-label">
            {d.label} · {date}
          </span>
          <StatusBadge tone={STATE_TONE[live?.state ?? '상태 미제공']}>
            {live?.state ?? '상태 미제공'}
          </StatusBadge>
          <InfoPopover
            label="판정 출처"
            title="판정 출처"
            text={
              `판정 출처: ${d.cadence.kind === 'intradayWindows' ? d.cadence.ledger : '—'}\n\n` +
              (live
                ? '이 원장에는 최근 7일 일별 요약 엔드포인트가 없다. 다만 세션 응답이 이 날짜의\n' +
                  '세션을 주므로 그 실행체 생존 판정만 그대로 편다:\n' +
                  `${live.basis}\n\n` +
                  '창 카운트로 그날의 귀결(정상·주의)을 만들지는 않는다 — 진행 중인 세션에\n' +
                  '완결 판정을 붙이는 셈이라 근거가 없다. 분·poll 단위는 세션 상세가 답한다.'
                : '이 원장에는 최근 7일 일별 요약 엔드포인트가 없어 격자가 이 날짜의 판정 값을 받지 못했다.\n' +
                  '데이터 출처가 다른 것은 운영 상태가 아니므로 상태 어휘로 쓰지 않는다 —\n' +
                  '없는 판정을 지어내는 대신 "상태 미제공"이라고 쓰고 세션 상세로 보낸다.')
            }
          />
          <button type="button" className="btn btn-sm" style={{ marginLeft: 'auto' }} onClick={onClose}>
            닫기
          </button>
        </div>
        <div className="card-pad">
          {minuteDetail?.kind === 'ready' && minuteDetail.refreshFailed && (
            <p className="t-xs" style={{ color: 'var(--fg-3)', marginTop: 0 }}>
              세션 갱신에 실패해 이 날짜의 직전 실측을 유지합니다.
            </p>
          )}
          <table className="table">
            <thead>
              <tr>
                <th>실행 인스턴스</th>
                <th>유형</th>
                <th>상태</th>
                <th>창 증거</th>
                <th>상세</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((session) => {
                const state = realtimeSessionState(session);
                const sessionHref = minuteSessionHref(date, d.sessionDataset!, session.sourceGroup);
                return (
                  <tr key={session.sessionId}>
                    <td className="mono">
                      {d.sessionDataset} / {session.sourceGroup} · {date}
                    </td>
                    <td><span className="chip">실시간 세션</span></td>
                    <td><StatusBadge tone={STATE_TONE[state.state]}>{state.state}</StatusBadge></td>
                    <td className="t-xs">
                      정상 {session.windows.valid} · 빈 데이터 {session.windows.validEmpty} · 불완전{' '}
                      {session.windows.incomplete} · 결손 {session.windows.missing} · 무효{' '}
                      {session.windows.invalid} · 무증거 {session.windows.overdueNoEvidence} / 기대{' '}
                      {session.expectedWindowCount}
                    </td>
                    <td><Link to={sessionHref} className="gd-linkbtn">세션 상세 →</Link></td>
                  </tr>
                );
              })}
              {minuteDetail?.kind === 'ready' && sessions.length === 0 && (
                <tr>
                  <td colSpan={4}>이 날짜에 기록된 {d.label} 벤더 세션이 없습니다.</td>
                  <td><Link to={href} className="gd-linkbtn">날짜 상세 →</Link></td>
                </tr>
              )}
              {detailMessage && (
                <tr>
                  <td colSpan={4}>{detailMessage}</td>
                  <td><Link to={href} className="gd-linkbtn">날짜 상세 →</Link></td>
                </tr>
              )}
              {!minuteDetail && mock && (
                <tr>
                  <td className="mono">{d.sessionDataset} · {date}</td>
                  <td><span className="chip">실시간 세션</span></td>
                  <td><StatusBadge tone={STATE_TONE['상태 미제공']}>상태 미제공</StatusBadge></td>
                  <td>—</td>
                  <td><Link to={href} className="gd-linkbtn">세션 상세 →</Link></td>
                </tr>
              )}
            </tbody>
          </table>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 8 }}>
            실행 인스턴스는 <b>데이터셋 × 벤더 × 세션 날짜</b>입니다 — 1분 창·poll 은 각 세션의 하위
            증거라 여기서 실행으로 펼치지 않습니다.
          </p>
        </div>
      </div>
    );
  }

  if (!r) {
    return (
      <div className="card" id="gd-detail">
        <div className="card-head">
          <span className="t-label">
            {d.label} · {date}
          </span>
          <StatusBadge tone={STATE_TONE['계획 없음']}>계획 없음</StatusBadge>
          <button type="button" className="btn btn-sm" style={{ marginLeft: 'auto' }} onClick={onClose}>
            닫기
          </button>
        </div>
        <div className="card-pad">
          <p className="t-sm m-0" style={{ color: 'var(--fg-3)' }}>
            이 날짜에 이 데이터셋의 계획 행이 없습니다 — 실행 계획 자체가 없었다는 사실입니다.
          </p>
        </div>
      </div>
    );
  }

  const byState = (st: DayState) => r.executions.filter((e) => e.state === st).length;
  const summary: [DayState, number][] = (
    ['정상', '주의', '장애', '실행 중', '대기', '계획 스킵'] as DayState[]
  )
    .map((st) => [st, byState(st)] as [DayState, number])
    .filter(([, n]) => n > 0);

  return (
    <div className="card" id="gd-detail">
      <div className="card-head">
        <span className="t-label">
          {d.label} · {date}
        </span>
        <StatusBadge tone={STATE_TONE[r.state]}>{r.state}</StatusBadge>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          실행 {r.executions.length}회 · 기대 {r.expected}회 · {d.cadence.label}
          <InfoPopover
            label="실행 수"
            title="실행 수 세는 법"
            text={
              '실행 인스턴스는 runKey 하나다 — 한 런에 수집·정제·적재 작업이 3개 있어도 실행은 1회다.\n' +
              '기대 실행은 계획(plan_status=DUE)이 있던 실행 인스턴스 수이고, 작업 수가 아니다.\n' +
              '계획 슬롯인데 런 행이 없으면 그 슬롯 하나가 실행 인스턴스 한 건으로 선다.'
            }
          />
        </span>
        <button type="button" className="btn btn-sm" style={{ marginLeft: 'auto' }} onClick={onClose}>
          닫기
        </button>
      </div>
      <div className="card-pad">
        <div className="gd-figures">
          {summary.map(([st, n]) => (
            <span key={st} className="gd-figure">
              <StateBox state={st} /> {st} <b>{n}</b>
            </span>
          ))}
        </div>

        <ul className="gd-exec-list">
          {r.executions.map((e) => (
            <ExecutionRow key={e.runKey} exec={e} mock={mock} />
          ))}
        </ul>

        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 10 }}>
          빈 데이터 증거는 <b>돌았고 데이터가 없었다</b>는 사실이고, 무증거는{' '}
          <b>기한이 지났는데 증거가 없다</b>는 사실입니다 — 합쳐 세지 않습니다. 같은 날 런이
          여럿이면 실행이 여러 건으로 섭니다. 그중 무엇이 정규·수동·백필인지는 원장에 기록이 없어
          구분하지 않습니다.
        </p>
      </div>
    </div>
  );
}

/** 실행 하나 — 접힘이 기본이고 문제 있는 실행만 펼친 채로 시작한다 */
function ExecutionRow({ exec, mock }: { exec: DayExecution; mock: boolean }) {
  const problem = exec.state === '장애' || exec.state === '주의';
  const [open, setOpen] = useState(problem);
  const c = exec.counts;

  const problems = c.failed + c.noEvidence;
  const facts = [
    problems > 0 ? `문제 작업 ${problems}` : null,
    c.running > 0 ? `실행 중 ${c.running}` : null,
    c.incomplete + c.invalid > 0 ? `부분 결손 ${c.incomplete + c.invalid}` : null,
    c.skipped > 0 ? `계획 제외 ${c.skipped}` : null,
    `전체 작업 ${exec.tasks.length}`,
  ].filter(Boolean);

  return (
    <li className="gd-exec">
      <button
        type="button"
        className="gd-exec-head"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span aria-hidden="true">{open ? '▾' : '▸'}</span>
        <span className="mono t-sm">{exec.runKey}</span>
        <span className="chip">배치 실행</span>
        <StatusBadge tone={STATE_TONE[exec.state]}>{exec.state}</StatusBadge>
        {mock && <MockChip />}
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          {facts.join(' · ')}
        </span>
      </button>
      {open && (
        <>
        <div style={{ overflowX: 'auto' }}>
          <table className="table">
            <thead>
              <tr>
                <th>단계</th>
                <th>작업</th>
                <th>귀결</th>
                <th>데이터</th>
                <th className="num">산출</th>
                <th className="num">유실</th>
                <th>사유</th>
              </tr>
            </thead>
            <tbody>
              {exec.tasks.map((t) => (
                <tr key={t.taskKey}>
                  <td className="col-muted">{STAGE_LABEL[t.stage] ?? t.stage}</td>
                  <td className="mono">{t.taskKey}</td>
                  <td>
                    {t.planStatus === 'SKIPPED'
                      ? '계획 제외'
                      : t.running
                        ? '실행 중'
                        : (OUTCOME_LABEL[t.outcome ?? ''] ?? t.outcome ?? '판정 없음')}
                  </td>
                  <td className="col-muted">{t.dataStatus ?? '—'}</td>
                  <td className="num">{t.recordsOut ?? '—'}</td>
                  <td className="num">{t.failedRecords ?? '—'}</td>
                  <td className="col-muted t-xs">{t.reason ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {/* 행마다 반복하지 않는다 — 값이 행에 따라 변하지 않는 사실이고, 표 안에 두면
          * 스크린리더가 작업 수만큼 같은 문장을 읽는다. 빈 칸으로 두지도 않는다.
          * 가로 스크롤 박스 **밖**에 둔다 — 안에 두면 줄바꿈 폭을 표가 정해서, 표가
          * 넓은 화면에서는 이 문장을 끝까지 읽으려고 가로 스크롤해야 한다. */}
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', padding: '6px 0 2px' }}>
          {RUN_DETAIL_UNAVAILABLE} — 이 표가 이 실행에 대해 답할 수 있는 전부입니다.
        </p>
        </>
      )}
    </li>
  );
}
