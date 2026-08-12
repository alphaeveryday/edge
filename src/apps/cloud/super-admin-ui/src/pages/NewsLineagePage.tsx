/* 뉴스 계보 — Dataset Explorer 첫 슬라이스 (ALPHA-685, 지표 산출 명세화 ALPHA-697).
 *
 * 존재 이유: "표시된 집계값을 목록으로 검증할 길"(멘토: "4천 건은 어디 있어"). 그래서 이
 * 화면의 모든 숫자는 (i)에 산출 정의를 달고, 타일 클릭이 그 부분집합 목록으로 내려간다 —
 * 목록 없는 집계를 만들지 않는다. 비율은 단독 표시하지 않고 항상 N/M 을 병기한다(분모 없는
 * 퍼센트는 오독의 통로). 비율 계산은 서버가 내린 두 카운트의 산술 표현일 뿐 판정이 아니다.
 *
 * 주장의 한계(정직 표기): 원장(RDS)이 아는 건 문서의 존재 → 구조화 증거(assertion) →
 * 분석 사용까지다. "증거 없음"은 NO_EVENT·추출 실패·미실행이 구분 없이 섞인 한 통이다 —
 * 그 (i) 툴팁이 이 한계를 말한다(승격은 후속 티켓).
 */
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { PageSkeleton } from 'ui-kit';
import type { NewsLineage, NewsLineageDocument, NewsLineageStage } from '../domains/sources';
import { useMinuteStatus, useNewsLineage } from '../domains/sources/hooks';
import { hasPendingJobs, healthyClaimed } from '../domains/sources/minuteView';
import { mockLineage } from '../mock/preview';
import { EmptyRealNotice, MockChip, MockPreview } from './_shared/MockPreview';
import { InfoPopover } from './_shared/InfoPopover';
import { LoadError } from './_shared/LoadError';

const fmt = (iso: string | null) =>
  iso ? `${new Date(iso).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })}` : '—';

/** 분모 0 이면 '—' — 0/0 을 0% 로 표기하면 "확인했고 정상"으로 읽힌다. */
const pct = (n: number, m: number) => (m > 0 ? `${((n / m) * 100).toFixed(1)}%` : '—');

function DocumentRow({ d }: { d: NewsLineageDocument }) {
  return (
    <tr>
      <td style={{ padding: '3px 10px 3px 0', maxWidth: 420, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={d.title ?? ''}>
        {d.sourceUri ? (
          <a href={d.sourceUri} target="_blank" rel="noreferrer">{d.title ?? d.sourceUri}</a>
        ) : (
          d.title ?? '—'
        )}
      </td>
      <td style={{ padding: '3px 10px 3px 0', whiteSpace: 'nowrap' }}>{d.publisher ?? '—'}</td>
      <td style={{ padding: '3px 10px 3px 0', whiteSpace: 'nowrap' }}>{d.sourceCode ?? '—'}</td>
      <td style={{ padding: '3px 10px 3px 0', whiteSpace: 'nowrap' }}>{fmt(d.publishedAt)}</td>
      <td style={{ padding: '3px 10px 3px 0', whiteSpace: 'nowrap' }}>{fmt(d.availableAt)}</td>
      <td style={{ padding: '3px 10px 3px 0', textAlign: 'right' }}>
        {d.assertionCount > 0 ? `${d.assertionCount}건` : (
          /* 0 이 아니라 "없음" — NO_EVENT·실패·미실행을 여기서 가를 수 없다는 표기다 */
          <span style={{ color: 'var(--fg-3)' }}>없음</span>
        )}
      </td>
      <td style={{ padding: '3px 0', textAlign: 'center' }}>
        {d.usedInAnalysis ? '사용' : <span style={{ color: 'var(--fg-3)' }}>—</span>}
      </td>
    </tr>
  );
}

/** funnel 타일 — 클릭=그 단계 목록 필터, (i)=산출 정의(분자·분모·한계). */
function StageTile({ label, value, info, active, onClick }: {
  label: string;
  value: string;
  info: string;
  active: boolean;
  onClick: () => void;
}) {
  /* 산출 정의 (i) 는 타일 버튼 **밖**에 둔다 — 버튼 안에 버튼을 넣을 수 없고,
   * 넣으면 (i) 클릭이 단계 필터까지 같이 토글한다. */
  return (
    <span
      className="t-sm"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 2,
        border: `1px solid ${active ? 'var(--fg-1, #111)' : 'var(--border, #d1d5db)'}`,
        borderRadius: 6,
        padding: '6px 10px',
      }}
    >
      <button
        type="button"
        onClick={onClick}
        className="t-sm"
        style={{ border: 0, background: 'none', padding: 0, font: 'inherit', cursor: 'pointer', textAlign: 'left' }}
      >
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>{label}</span>
        <br />
        <b>{value}</b>
      </button>
      <InfoPopover text={info} label={label} title={`${label} 산출 정의`} />
    </span>
  );
}

const STAGE_LABEL: Record<NewsLineageStage, string> = {
  structured: '구조화 증거 있음',
  unstructured: '구조화 증거 없음',
  used: '분석 사용',
};

export function NewsLineagePage() {
  /* 기본은 전체 누적 — 런 단위 계보는 불가하다(문서 테이블에 run_id 없음), 날짜로 자른다.
   * ?date= 는 다른 화면(Run Overview 뉴스 레인)이 특정 날짜로 내려보내는 손잡이다(ALPHA-692) —
   * 초기값만 받고 이후 변경은 로컬 상태(기존 동작 유지). */
  const [params] = useSearchParams();
  const [date, setDate] = useState<string>(params.get('date') ?? '');
  /* 표본 크기 — 서버 상한 200. 전량 페이지네이션은 후속(표본 검증 경로가 이 화면의 계약) */
  const [limit, setLimit] = useState<number>(50);
  /* 타일 클릭 드릴다운(ALPHA-697) — 필터는 목록만 좁히고 집계 타일 분모는 유지된다(서버 계약) */
  const [stage, setStage] = useState<NewsLineageStage | undefined>(undefined);
  const { data, isPending, isError, error } = useNewsLineage(date || undefined, limit, stage);
  /* 🔴 **계보 응답만으로는 "0 건"의 뜻을 못 가른다.** 추출 요약이 terminal 두 칸
   * (`succeeded`·`dead`)만 세는데, 문서가 아직 안 나온 시점의 job 은 PENDING/RETRY_WAIT/
   * CLAIMED 뿐이라 둘 다 0 이다 — 정상 운영 중인 날이 "볼 것 없음"으로 접힌다.
   * 나머지 칸은 `/sources/minute` 의 같은 날짜 집계가 답한다: `news_extraction_job` 은 한
   * 테이블이고 날짜 축도 하나다(`created_at` 의 KST 반개구간 — `JdbcNewsLineageRepository`
   * 자바독이 "`/minute` 콘솔과 같은 규칙"이라고 적어 뒀다). 그래서 새 서버 축이 필요 없다. */
  const minute = useMinuteStatus(date || undefined);

  if (isError) return <LoadError error={error} />;
  if (isPending) return <PageSkeleton rows={6} />;

  const ext = data.extraction;
  /* 미종결 job 을 못 읽었으면(대기·실패) **진행 중이 아니라고 단정하지 않는다** — 모름을
   * "없음" 으로 접는 순간 목이 실을 덮는 그 자리로 돌아간다.
   *
   * 🔴 **날짜를 안 고르면 두 축이 안 맞는다.** 이 화면은 날짜가 없으면 **누적 전체**를 보는데
   * (`SourceService.newsLineage` 는 `date==null` 이면 날짜 필터를 안 건다), `/sources/minute` 는
   * `date==null` 이면 **오늘 하루**다(`LocalDate.now(KST)`). 그래서 어제 만들어진 미종결 job 은
   * 오늘 집계에 0 으로 나오고, 그걸 "미종결 없음"으로 읽으면 **누적 화면이 백로그를 목으로
   * 덮는다** — 고치려던 그 결함이 날짜 없는 축에서 그대로 재현된다. 전 기간 미종결 수를 주는
   * 조회가 없으므로, 누적 보기에서는 **모른다**고 말한다. */
  const cumulative = !date;
  /* ⭐ **0 과 양수는 대칭이 아니다.** 오늘 집계는 누적의 **부분집합**이라, 오늘 미종결이
   * 있으면 누적에도 확실히 있다(양수는 확정). 반대로 오늘 0 은 어제 것을 못 본 것이라
   * 아무것도 증명하지 않는다(0 은 모름). 그래서 `pending` 은 두 보기에서 똑같이 읽고,
   * **모름은 `pendingUnknown` 이 따로 진다** — `cumulative` 로 `pending` 을 통째로 눌러
   * 버리면 알아낸 진행 중까지 버려 목이 실 작업을 덮는다. */
  const pending = minute.data ? hasPendingJobs(minute.data.newsJobs) : false;
  const pendingUnknown = !pending && (cumulative || minute.isPending || minute.isError);

  const noDocs = data.summary.totalDocuments === 0 && data.documents.length === 0;

  /* 목 미리보기를 띄우는 조건. **막는 것은 "모름"이 아니라 "진행 중임을 알아낸 것"** 이다 —
   * 모름까지 막으면 날짜를 안 고른 기본 보기에서 미리보기가 영영 안 떠, 초기 환경 검수라는
   * 이 장치의 존재 이유가 사라진다. 대신 모를 때는 **안내 문구가 그 모름을 말한다**. */
  const empty = noDocs && ext.succeeded + ext.dead === 0 && !pending;

  /* 진행 중이거나 모르는 상태는 **목으로 덮지 않고 그 사실을 얹는다**(Rule 12).
   * ⚠️ 막다른 카드로 만들지 않는다 — 실 `LineageBody` 를 그대로 그려야 날짜·표본·단계
   * 컨트롤이 살아 있어 운영자가 다른 날짜로 나갈 수 있다(그 화면은 0건도 정직하게 그린다). */
  if (!empty && noDocs) {
    return (
      <div className="flex flex-col gap-4">
        <div className="card card-pad">
          <p className="t-sm m-0" style={{ fontWeight: 600 }}>
            {date ? `이 날짜(${date})의 문서가 아직 0건입니다.` : '문서가 아직 0건입니다.'}
          </p>
          {/* 🔴 세 갈래는 **서로 다른 사실**이다. `pending` 이 아니면 전부 "못 읽었다"로 접으면,
              추출이 이미 끝난 날(terminal 만 있는 날)에 대고 **없는 관측 공백을 조사하러**
              보낸다 — 그 수는 바로 아래 표에 있는데도. */}
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
            {pending
              ? /* ⚠️ `claimed` 를 그대로 쓰면 안 된다 — `claimedExpired` 가 그 부분집합이라
                 * lease 가 전부 끊긴 날에도 "처리 중 N건"이 뜬다. 개요 카드와 같은
                 * `healthyClaimed` 를 쓰고 고착은 따로 밝힌다(기다릴 일과 조사할 일이 다르다). */
                /* ⚠️ 누적 보기에서 이 수는 **오늘치**다(그 조회가 날짜 단위뿐이라 그렇다).
                 * 라벨 없이 적으면 "누적 미종결이 이만큼"으로 읽혀 또 같은 오독이 된다 —
                 * 진행 중이라는 **사실**만 확정이고 수는 하한이다. */
                `추출이 진행 중입니다(${cumulative ? '오늘치 기준 — 이전 날짜는 이 수에 없습니다' : `${date} 기준`}) — 대기 ${
                  minute.data!.newsJobs.waiting
                }건 · 처리 중 ${healthyClaimed(minute.data!.newsJobs)}건${
                  minute.data!.newsJobs.claimedExpired > 0
                    ? ` · 고착 ${minute.data!.newsJobs.claimedExpired}건(유효 lease 없음 — 기다릴 것이 아니라 확인할 것입니다)`
                    : ''
                }. 미가동이 아니고, 목데이터로 대체하지도 않습니다.`
              : pendingUnknown
                ? cumulative
                  ? '누적 보기에서는 미종결 추출 job 수를 알 수 없습니다 — 그 수를 주는 조회가 날짜 단위뿐입니다. 날짜를 고르면 진행 중 여부를 말할 수 있습니다.'
                  : '추출 job 상태를 못 읽었습니다 — 진행 중인지 미가동인지 지금은 알 수 없습니다.'
                : `추출은 이 날짜에 이미 끝났습니다 — 성공 ${ext.succeeded}건 · 실패 ${ext.dead}건. 문서가 0건인 것은 진행 중이어서가 아닙니다.`}
            {' '}아래는 실 응답이며 목데이터가 아닙니다.
          </p>
        </div>
        <LineageBody
          data={data}
          date={date}
          setDate={setDate}
          limit={limit}
          setLimit={setLimit}
          stage={stage}
          setStage={setStage}
        />
      </div>
    );
  }

  if (empty) {
    return (
      <div className="flex flex-col gap-4">
        {/* ⚠️ 이 경로는 **"진행 중을 알아내지 못했을 때"도 통과한다**(누적 보기·조회 실패).
            그러니 "미가동"이라 단정하지 말고 확인한 범위를 그대로 적는다 — 아래 목데이터를
            "지금 아무 일도 없다"의 근거로 읽으면 안 된다. */}
        <EmptyRealNotice>
          {date
            ? pendingUnknown
              ? `이 날짜(${date})에 수집된 문서가 없습니다 — 미종결 추출 job 이 있는지는 확인하지 못했습니다(1분 원장 조회 실패).`
              : `이 날짜(${date})에 수집된 문서가 없고, 진행 중인 추출 job 도 없습니다.`
            : '수집된 문서가 없습니다 — 원장(document)에 뉴스가 아직 적재되지 않았습니다. 누적 보기라 미종결 추출 job 유무는 알 수 없습니다(날짜를 고르면 답할 수 있습니다).'}
        </EmptyRealNotice>
        <MockPreview>
          <LineageBody
            data={mockLineage(stage, limit)}
            date={date}
            setDate={setDate}
            limit={limit}
            setLimit={setLimit}
            stage={stage}
            setStage={setStage}
            mock
          />
        </MockPreview>
      </div>
    );
  }

  return (
    <LineageBody
      data={data}
      date={date}
      setDate={setDate}
      limit={limit}
      setLimit={setLimit}
      stage={stage}
      setStage={setStage}
    />
  );
}

function LineageBody({
  data,
  date,
  setDate,
  limit,
  setLimit,
  stage,
  setStage,
  mock = false,
}: {
  data: NewsLineage;
  date: string;
  setDate: (v: string) => void;
  limit: number;
  setLimit: (v: number) => void;
  stage: NewsLineageStage | undefined;
  setStage: (v: NewsLineageStage | undefined) => void;
  mock?: boolean;
}) {
  const s = data.summary;
  const m = s.totalDocuments;
  const unstructured = m - s.documentsWithAssertion;
  const ex = data.extraction;
  const exTotal = ex.succeeded + ex.dead;
  const dateScope = data.date ? `수집일(KST)=${data.date}` : '전체 누적';
  const toggle = (next: NewsLineageStage) => setStage(stage === next ? undefined : next);

  return (
    <div className="flex flex-col gap-4">
      <div className="card">
        <div className="card-head">
          <span className="t-label">뉴스 계보 {mock && <MockChip />}</span>
          {/* 네이티브 date input — 라이브러리 불요 */}
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="t-xs"
            style={{ border: '1px solid var(--border, #d1d5db)', borderRadius: 4, padding: '2px 6px' }}
          />
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            {data.date ? `수집일(KST) ${data.date}` : '전체 누적'} · 단위=문서(기사) · 타일 클릭=그 단계 목록
          </span>
        </div>

        {/* funnel — 비율은 항상 N/M 병기, 산출 정의는 (i) */}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <StageTile
            label="수집"
            value={`${m.toLocaleString()}건`}
            info={`document(NEWS) 중 수집 시각(available_at)의 KST 날짜가 조건(${dateScope})인 문서 수.`}
            active={stage === undefined}
            onClick={() => setStage(undefined)}
          />
          <StageTile
            label="구조화 증거 있음"
            value={`${s.documentsWithAssertion.toLocaleString()}/${m.toLocaleString()} (${pct(s.documentsWithAssertion, m)})`}
            info={'분자=구조화 증거(document_assertion)가 1건 이상 남은 문서 · 분모=수집 문서. "추출 성공"이 아니라 증거가 남았다는 사실이다.'}
            active={stage === 'structured'}
            onClick={() => toggle('structured')}
          />
          <StageTile
            label="구조화 증거 없음"
            value={`${unstructured.toLocaleString()}/${m.toLocaleString()} (${pct(unstructured, m)})`}
            info={'분자=증거 0건 문서 · 분모=수집 문서. 이벤트 없는 정상 기사(NO_EVENT)·추출 실패·미실행이 구분 없이 섞인 한 통이다 — 문서별 추출 판정은 아직 원장에 없다(승격 후속).'}
            active={stage === 'unstructured'}
            onClick={() => toggle('unstructured')}
          />
          <StageTile
            label="분석 사용"
            value={`${s.documentsUsedInAnalysis.toLocaleString()}/${m.toLocaleString()} (${pct(s.documentsUsedInAnalysis, m)})`}
            info={'분자=assertion→event_evidence→explanation_run 체인이 존재하는 문서 · 분모=수집 문서.'}
            active={stage === 'used'}
            onClick={() => toggle('used')}
          />
        </div>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 6 }}>
          ⚠️ 원장이 답할 수 있는 범위: 존재 → 증거 → 분석 사용. 중복 제거·종목 연결 단계는 계측 밖이다.
        </p>
      </div>

      {/* 장중 1분 추출 — 문서 표와 다른 원장(news_extraction_job)임을 명시한다 */}
      <div className="card">
        <div className="card-head">
          <span className="t-label">장중 1분 추출 job</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            산출 기준
            <InfoPopover
              label="산출 기준"
              text={`news_extraction_job 기준 · 날짜 축=job 생성 시각(KST, ${dateScope}) — 위 문서 표(수집 시각 축)와 다른 원장이라 분모가 다를 수 있다. EOD 레인 실패는 작업 단위로 파이프라인 실행 이력(/sources) 소관.`}
            />
          </span>
        </div>
        {exTotal === 0 ? (
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            귀결(SUCCEEDED/DEAD)된 추출 job 없음 — 1분 파이프라인 미가동과 진행 중(대기·재시도)이
            구분되지 않는 0 이다. 진행 상태는 장중 1분 수집(/minute)이 답한다.
          </p>
        ) : (
          <>
            <p className="t-sm m-0">
              성공 <b>{ex.succeeded.toLocaleString()}건</b>
              {' · '}DEAD <b style={{ color: ex.dead > 0 ? 'var(--down, #b91c1c)' : undefined }}>
                {ex.dead.toLocaleString()}건
              </b>
              {' · '}실패 비중 <b>{ex.dead.toLocaleString()}/{exTotal.toLocaleString()} ({pct(ex.dead, exTotal)})</b>
              <span className="t-xs" style={{ color: 'var(--fg-3)' }}> — 분모=귀결(성공+DEAD)된 job</span>
            </p>
            {ex.deadByErrorCode.length > 0 && (
              <p className="t-xs m-0" style={{ marginTop: 4 }}>
                DEAD 사유별:{' '}
                {ex.deadByErrorCode.map((c, i) => (
                  <span key={c.errorCode ?? '(null)'}>
                    {i > 0 && ' · '}
                    <code>{c.errorCode ?? '(사유 미기록)'}</code> {c.count.toLocaleString()}건
                  </span>
                ))}
              </p>
            )}
          </>
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">문서 목록 {mock && <MockChip />}</span>
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="t-xs"
            style={{ border: '1px solid var(--border, #d1d5db)', borderRadius: 4, padding: '2px 4px' }}
            title="표본 크기 (서버 상한 200)"
          >
            <option value={50}>50건</option>
            <option value={200}>200건</option>
          </select>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            {stage ? `필터: ${STAGE_LABEL[stage]} · ` : ''}수집 시각 내림차순 · 최근{' '}
            {data.documents.length}건 표본 — 위 집계의 검증 경로
          </span>
        </div>
        {data.documents.length === 0 ? (
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            {stage
              ? '이 단계에 해당하는 문서가 없습니다.'
              : data.date ? '이 날짜에 수집된 문서가 없습니다.' : '수집된 문서가 없습니다.'}
          </p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', fontSize: 12, width: '100%' }}>
              <thead>
                <tr className="t-xs" style={{ color: 'var(--fg-3)', textAlign: 'left' }}>
                  <th style={{ padding: '0 10px 4px 0' }}>제목</th>
                  <th style={{ padding: '0 10px 4px 0' }}>언론사</th>
                  <th style={{ padding: '0 10px 4px 0' }}>벤더</th>
                  <th style={{ padding: '0 10px 4px 0' }}>게시(KST)</th>
                  <th style={{ padding: '0 10px 4px 0' }}>수집(KST)</th>
                  <th style={{ padding: '0 10px 4px 0', textAlign: 'right' }}>구조화 증거</th>
                  <th style={{ padding: '0 0 4px 0' }}>분석 사용</th>
                </tr>
              </thead>
              <tbody>
                {data.documents.map((d) => (
                  <DocumentRow key={d.documentId} d={d} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
