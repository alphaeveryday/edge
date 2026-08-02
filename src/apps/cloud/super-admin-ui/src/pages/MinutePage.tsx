/* 장중 1분 수집 — "지금 이 순간에도 증거가 남고 있는가" (ALPHA-651).
 *
 * 존재 이유: 카탈로그 27작업은 배치 전제라 매분 도는 상주 실행체(Price Collector·News
 * Scanner)는 ops 원장의 사각이다. 이 화면은 1분 원장(minute_*)의 요약 관측이다 — ops 행
 * 복제가 아니라(계획 §2-1), 세션·창 집계·결손 창 목록을 그대로 낸다.
 *
 * 핵심 구분(관대화 금지): "안 돌았다"(무증거 — 기한 지난 DUE/CLAIMED, 서버 판정)와
 * "돌았는데 데이터 없음"(VALID_EMPTY — 거래 없는 분의 정상 결과)은 다른 사실이다.
 * MISSING 판정은 EOD QC 몫이라 장중 결손은 무증거 파생으로만 보인다.
 */
import { useState } from 'react';
import { PageSkeleton } from 'ui-kit';
import type { MinuteJobCounts, MinuteSession } from '../domains/sources';
import { useMinuteStatus } from '../domains/sources/hooks';
import { LoadError } from './_shared/LoadError';

const fmtTime = (iso: string | null) =>
  iso
    ? new Date(iso).toLocaleTimeString('ko-KR', { timeZone: 'Asia/Seoul', hour12: false })
    : '—';

/** 실행체 생존 표시 — leaseExpired 는 서버 판정이고 여기선 라벨만 고른다(재계산 금지) */
function liveness(s: MinuteSession): { label: string; tone: 'ok' | 'bad' | 'muted' } {
  /* 종료 국면에선 lease 만료가 정상이다(drain ack 이후 실행체는 떠나는 게 맞음) — phase 를
   * 무시하면 정상 종료·과거 날짜 세션이 전부 "증거 끊김"으로 보인다(리뷰 1라운드). */
  if (s.phase === 'FAILED') return { label: '세션 FAILED', tone: 'bad' };
  if (s.phase === 'DRAINED' || s.phase === 'QC_RUNNING' || s.phase === 'FINALIZED') {
    return { label: `세션 종료 국면 (${s.phase}) — 생존 판정 대상 아님`, tone: 'muted' };
  }
  if (s.leaseExpired === true) return { label: '실행체 증거 끊김 (lease 만료)', tone: 'bad' };
  if (s.leaseExpired === false) return { label: `가동 중 · heartbeat ${fmtTime(s.heartbeatAt)}`, tone: 'ok' };
  /* null = lease 부재 — "죽었다"가 아니라 기동 증거 자체가 없다는 사실 */
  return { label: '기동 증거 없음 (lease 미획득)', tone: 'muted' };
}

const TONE_COLOR = { ok: 'var(--ok, #16a34a)', bad: 'var(--bad, #dc2626)', muted: 'var(--fg-3)' };

function JobCells({ jobs }: { jobs: MinuteJobCounts }) {
  return (
    <>
      대기 {jobs.waiting} · 처리 중 {jobs.claimed}
      {/* lease 만료된 claim = Consumer 가 죽고 아무도 재청구 안 한 고착 후보 — "처리 중"에
       * 뭉개면 영원히 경고가 없다(리뷰 1라운드) */}
      {jobs.claimedExpired > 0 && (
        <b style={{ color: 'var(--bad, #dc2626)' }}> (그중 lease 만료 {jobs.claimedExpired})</b>
      )}
      {' '}· 성공 {jobs.succeeded} ·{' '}
      <b style={{ color: jobs.dead > 0 ? 'var(--bad, #dc2626)' : undefined }}>DEAD {jobs.dead}</b>
    </>
  );
}

function SessionCard({ s }: { s: MinuteSession }) {
  const live = liveness(s);
  const w = s.windows;
  const evidenced = w.valid + w.validEmpty + w.incomplete + w.invalid;
  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">
          {s.dataset} / {s.sourceGroup}
        </span>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          phase {s.phase} · universe {s.universeVersion}
        </span>
        <span className="t-xs" style={{ color: TONE_COLOR[live.tone], marginLeft: 'auto' }}>
          {live.label}
        </span>
      </div>

      {/* 창 집계 — 단위는 창(1분). 각 숫자의 근거는 아래 결손 창 표다 */}
      <p className="t-sm m-0">
        기대 창 <b>{s.expectedWindowCount}개</b> 중 증거 남음 <b>{evidenced}개</b>
        {' '}(정상 {w.valid} · 빈 데이터 {w.validEmpty} · 불완전 {w.incomplete} · 무효 {w.invalid})
        {' · '}
        <b style={{ color: w.overdueNoEvidence > 0 ? 'var(--bad, #dc2626)' : undefined }}>
          무증거 {w.overdueNoEvidence}개
        </b>
        {w.missing > 0 && <> · MISSING(EOD 판정) {w.missing}개</>}
        {/* 기한 전 DUE/CLAIMED — 아직 판정 대상이 아닌 정상 대기 */}
        {' · '}대기 {w.due + w.claimed - w.overdueNoEvidence}개
      </p>
      <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
        연속 완결 워터마크 {fmtTime(s.contiguousCompleteThrough)} · 마지막 기록{' '}
        {fmtTime(s.processedThrough)} · 가격 job: <JobCells jobs={s.priceJobs} />
      </p>
      <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
        "무증거"는 기한이 지났는데 결과가 안 적힌 창(안 돌았거나 실행체가 죽음)이고, "빈
        데이터"는 돌았는데 그 분에 거래가 없었다는 <b>증거가 남은</b> 창이다 — 다른 사실이라
        합쳐 세지 않는다.
      </p>

      {s.gaps.length > 0 && (
        <div style={{ overflowX: 'auto', marginTop: 8 }}>
          <table style={{ borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr className="t-xs" style={{ color: 'var(--fg-3)', textAlign: 'left' }}>
                <th style={{ padding: '0 10px 4px 0' }}>창 시작(KST)</th>
                <th style={{ padding: '0 10px 4px 0' }}>원장 상태</th>
                <th style={{ padding: '0 0 4px 0' }}>판정</th>
              </tr>
            </thead>
            <tbody>
              {s.gaps.map((g) => (
                <tr key={g.windowStart}>
                  <td style={{ padding: '2px 10px 2px 0', whiteSpace: 'nowrap' }}>
                    {fmtTime(g.windowStart)}
                  </td>
                  <td style={{ padding: '2px 10px 2px 0' }}>{g.dataStatus}</td>
                  <td style={{ padding: '2px 0', color: g.noEvidence ? 'var(--bad, #dc2626)' : undefined }}>
                    {g.noEvidence ? '무증거 (기한 경과, 결과 없음)' : '증거 있는 결함'}
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

export function MinutePage() {
  /* 기본은 오늘(서버 KST) — 세션은 하루 단위 identity 다 */
  const [date, setDate] = useState<string>('');
  const { data, isPending, isError, error } = useMinuteStatus(date || undefined);

  if (isError) return <LoadError error={error} />;
  if (isPending) return <PageSkeleton rows={6} />;

  return (
    <div className="flex flex-col gap-4">
      <div className="card">
        <div className="card-head">
          <span className="t-label">장중 1분 수집</span>
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="t-xs"
            style={{ border: '1px solid var(--border, #d1d5db)', borderRadius: 4, padding: '2px 6px' }}
          />
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            세션 날짜(KST) {data.date} · 단위=창(1분)
          </span>
        </div>
        {data.sessions.length === 0 ? (
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            이 날짜의 세션이 없습니다 — 1분 파이프라인이 계획되지 않았다는 사실이다(비거래일
            또는 미가동). 오류가 아니라 관측 결과다.
          </p>
        ) : (
          data.sessions.map((s) => <SessionCard key={s.sessionId} s={s} />)
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">뉴스 추출 job</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            기사 단위 논리 job · 날짜 축=job 생성 시각(KST) — 세션 연결 컬럼이 없어 별도 집계다
          </span>
        </div>
        <p className="t-sm m-0">
          <JobCells jobs={data.newsJobs} />
        </p>
      </div>
    </div>
  );
}
