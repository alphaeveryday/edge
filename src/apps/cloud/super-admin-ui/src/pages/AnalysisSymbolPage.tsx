/* 종목 상세 — 최신 유효 설명과 그 종목의 분석 이력 (ALPHA-738).
 *
 * 목록이 종목당 한 행으로 접히므로, 그 종목의 **읽을 설명 하나**와 **무슨 시도가 있었나**를
 * 여기서 편다.
 *
 * 지키는 선:
 *   · 최신 유효 설명은 **변동 기준 시각** 기준이다 — 늦게 끝난 과거 기준이 덮지 않는다.
 *   · 최신 시도가 실패해도 이전 유효 설명을 지우지 않는다. 실패한 시도를 누르면 분석 결과가
 *     아니라 **파이프라인 실행 이력**으로 보낸다(없는 결과를 여는 죽은 링크를 만들지 않는다).
 *   · 게시·발번 등 전달 축은 이 화면의 범위 밖이라 표시하지 않는다.
 *
 * 🔴 **이 화면이 보는 것은 원장 전량이 아니라 조회 창이다.** `useAnalyses` 는
 * `JdbcAnalysisRepository.LIST_SQL` 을 읽는데 거기엔 **종목 필터도 날짜 필터도 없고**
 * `explanation_as_of DESC LIMIT 200` 뿐이다. 즉 전 종목을 합친 최신 200건에서 이 종목 몫만
 * 걸러 본다. 그래서 이 화면은 두 가지를 **단정하면 안 된다**: ① "유효한 설명이 없다"(창 밖에
 * 있을 수 있다) ② "오늘의 이력"(창에는 어제 것도 섞여 있다). 둘 다 문구로 창을 밝힌다.
 */
import { Link, useSearchParams } from 'react-router-dom';
import { Delta, PageSkeleton, StatusBadge } from 'ui-kit';
import { ANALYSIS_CONFIDENCE_LABEL, ANALYSIS_STATUS_LABEL, ANALYSIS_STATUS_TONE } from '../domains/analyses';
import type { Analysis } from '../domains/analyses';
import { useAnalyses } from '../domains/analyses/hooks';
import { findGroup, hasResult } from '../domains/analyses/symbols';
import { MOCK_ANALYSES } from '../mock/preview';
import { MockChip, MockPreview } from './_shared/MockPreview';
import { LoadError } from './_shared/LoadError';

/** 조회 창 크기 — 서버 `JdbcAnalysisRepository.LIST_LIMIT` 과 같은 수. 문구가 이 값을 쓴다. */
const LIST_WINDOW = 200;

/**
 * 고객에게 실제로 나간 산문. **블록이 정본이고 `result` 는 폴백**이다(상세 화면과 같은 규약).
 *
 * ⚠️ `result` 만 쓰면 안 된다 — 서버는 `summary` 가 비면 `result` 를 안내 문장으로 바꿔 싣는다
 * (`AnalysisResponse.result`). 블록에 본문이 있고 `summary` 만 빈 행에서는 `hasResult` 가
 * **유효**로 세운 설명 자리에 "설명 본문이 원장에 없습니다"가 찍힌다 — 한 화면이 자기 판정과
 * 모순되는 문장을 쓰는 셈이다.
 */
function resultTexts(a: Analysis): string[] {
  const blocks = (a.resultBlocks ?? []).map((b) => b.text.trim()).filter((t) => t.length > 0);
  return blocks.length > 0 ? blocks : [a.result];
}

export function AnalysisSymbolPage() {
  const [params] = useSearchParams();
  /* 🔴 시장·코드는 **쿼리**로 받는다(`symbols.symbolHref` 가 그렇게 만든다) — 경로에 두면
   * 점 든 티커의 공유 링크가 CDN 에서 정적 파일로 갈려 죽는다. 거기 근거를 적어 뒀다. */
  const market = params.get('market') ?? '';
  const code = params.get('code') ?? '';
  const preview = params.get('preview') === 'mock';
  const query = useAnalyses();

  /* 🔴 **링크가 망가진 것과 종목이 조회 창 밖인 것은 다른 사실이다.** 빈 값으로 `findGroup`
   * 을 부르면 당연히 못 찾고, 화면은 "최신 200건 창에 없습니다"라고 말한다 — 실제로는 주소에
   * 종목이 없는 것이라 운영자가 **없는 종목을 조사하러 간다**(Rule 12).
   * ⚠️ 조회 자체를 막는 게 아니다 — `useAnalyses` 는 훅이라 위에서 이미 걸렸고, 조기 반환을
   * 훅 앞으로 올리면 렌더마다 훅 수가 갈린다. 여기서 가르는 것은 **응답의 해석**이다. */
  if (!market || !code) {
    return (
      <div className="card card-pad">
        <p className="t-sm m-0">종목이 지정되지 않은 주소입니다.</p>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
          이 화면은 <span className="mono">market</span>·<span className="mono">code</span> 를 주소에서
          받습니다 — 둘 중 하나가 비어 있어 어떤 종목도 조회하지 않았습니다(조회했는데 없는 것과
          다릅니다). <Link to="/analyses">가격 변동 분석 목록에서 다시 선택</Link>
        </p>
      </div>
    );
  }

  if (!preview && query.isError) return <LoadError error={query.error} />;
  if (!preview && query.isPending) return <PageSkeleton rows={6} />;

  const items = preview ? MOCK_ANALYSES : (query.data ?? []);
  const group = findGroup(items, market, code);

  if (!group) {
    return (
      <div className="card card-pad">
        {/* 🔴 **무엇을 뒤졌는지 밝힌다.** 미리보기에서는 실 원장이 아니라 목 픽스처를 뒤졌는데
            "최신 200건 조회 창에 없습니다"라고 말하면, 목 링크를 연 운영자가 **실 원장에 그
            종목이 없다**고 읽는다. 조회한 대상이 다르면 부재의 뜻도 다르다. */}
        <p className="t-sm m-0">
          {preview ? '이 종목이 목 픽스처에 없습니다.' : '이 종목의 분석이 조회 창 안에 없습니다.'}
        </p>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
          <span className="mono">
            {market} {code}
          </span>{' '}
          {preview ? (
            <>
              은 화면 검수용 목데이터에 없습니다 — 실 원장을 조회한 결과가 아닙니다.{' '}
            </>
          ) : (
            <>
              는 최신 {LIST_WINDOW}건 조회 창에 없습니다 — 이 종목의 분석이 없다는 뜻이 아니라
              그보다 오래됐을 수 있다는 뜻입니다.{' '}
            </>
          )}
          다른 종목으로 대체하지 않습니다. <Link to="/analyses">가격 변동 분석 목록으로</Link>
        </p>
      </div>
    );
  }

  const { latestValid, latestAttempt } = group;
  const detail = (id: string) => (preview ? `/analyses/${id}?preview=mock` : `/analyses/${id}`);

  const body = (
    <div className="flex max-w-[900px] flex-col gap-4">
      <nav className="t-xs ops-crumb" aria-label="조사 경로">
        {/* 목록은 `?preview=mock` 을 안 받는다 — 실데이터 0건일 때 스스로 목으로 떨어진다
            (`AnalysesPage`). 여기서 파라미터를 붙이면 목록이 안 읽는 축을 만드는 셈이다. */}
        <Link to="/analyses">가격 변동 분석 목록</Link>
        <span aria-hidden="true">›</span>
        <span style={{ color: 'var(--fg-1)' }}>
          {group.name} {group.code}
        </span>
      </nav>

      <div className="card">
        <div className="card-head">
          <span className="t-h3">{group.name}</span>
          <span className="mono t-xs" style={{ color: 'var(--fg-3)' }}>
            {group.code}
          </span>
          <span className="tag">{group.market}</span>
          {preview && <MockChip />}
          {/* "오늘"이라 쓰지 않는다 — 목록 SQL 에 날짜 조건이 없어 이 수에는 어제 것도 섞인다 */}
          <span className="t-xs" style={{ color: 'var(--fg-3)', marginLeft: 'auto' }}>
            분석 시도 {group.attemptCount}건
          </span>
        </div>
        <div className="card-pad">
          {latestValid ? (
            <>
              <span className="t-label">최신 유효 설명</span>
              {resultTexts(latestValid).map((t, i) => (
                <p key={i} className="t-body m-0 whitespace-pre-line" style={{ marginTop: 6 }}>
                  {t}
                </p>
              ))}
              <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 8 }}>
                기준 시각 <b>{latestValid.basisTime}</b> · 완료 {latestValid.doneTime} ·{' '}
                <Delta direction={latestValid.direction} pct={latestValid.changePct} />
                {latestValid.confidence && ` · 신뢰도 ${ANALYSIS_CONFIDENCE_LABEL[latestValid.confidence]}`}
                {' · '}
                사용 근거 {latestValid.evidenceTotal ?? latestValid.evidence.length}건
              </p>
              <p className="t-xs m-0" style={{ marginTop: 8 }}>
                <Link to={detail(latestValid.id)}>이 분석 상세 보기 →</Link>
              </p>
            </>
          ) : (
            <>
              <span className="t-label">최신 유효 설명</span>
              {/* 🔴 "없다"로 단정하지 않는다 — 조회 창이 전 종목 합산 최신 200건이라, 이 종목의
                  마지막 유효 설명이 창 밖으로 밀렸을 수 있다(이 종목의 실패 시도만 창 안에
                  남으면 정확히 그 모양이 된다). 부재를 실측처럼 쓰면 운영자가 "설명이 한 번도
                  안 나온 종목"으로 읽는다. */}
              <p className="t-sm m-0" style={{ color: 'var(--fg-3)', marginTop: 6 }}>
                조회 창(최신 {LIST_WINDOW}건) 안에 유효한 설명이 없습니다 — 이 종목에 설명이 없다는
                뜻이 아니라, 있었다면 창 밖으로 밀렸을 수 있다는 뜻입니다. 창 안의 시도는 아래
                이력에서 확인합니다.
              </p>
            </>
          )}

          {/* 최신 시도가 유효 결과가 아니면 그 사실을 따로 말한다(설명을 덮지 않는다) */}
          {group.attemptPending && (
            <p className="t-xs m-0" style={{ color: 'var(--fg-2)', marginTop: 10 }}>
              최근 생성 시도 <b>{latestAttempt.basisTime}</b>{' '}
              <StatusBadge tone={ANALYSIS_STATUS_TONE[latestAttempt.status]}>
                {ANALYSIS_STATUS_LABEL[latestAttempt.status]}
              </StatusBadge>{' '}
              — 위 설명은 그 이전 기준의 유효 결과입니다.
            </p>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          {/* 🔴 "오늘의"가 아니다 — 목록 SQL 에 날짜 조건이 없어 창에는 어제 것도 섞인다.
              행마다 기준 시각을 **날짜까지** 보여야 같은 시각의 다른 날 분석이 안 겹친다. */}
          <span className="t-label">분석 이력 {group.attemptCount}건</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            변동 기준 시각 최신순 · 최신 {LIST_WINDOW}건 조회 창 안 · 결과 없는 시도는 실행 이력으로
            갑니다
          </span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>기준 시각</th>
              <th className="col-num">등락률</th>
              <th>상태</th>
              <th>완료</th>
              <th className="col-num">사용 근거</th>
              <th>상세</th>
            </tr>
          </thead>
          <tbody>
            {group.analyses.map((a) => (
              <tr key={a.id}>
                <td className="num">{a.basisTime}</td>
                <td className="col-num">
                  <Delta direction={a.direction} pct={a.changePct} />
                </td>
                <td>
                  <StatusBadge tone={ANALYSIS_STATUS_TONE[a.status]}>
                    {ANALYSIS_STATUS_LABEL[a.status]}
                  </StatusBadge>
                </td>
                <td className="col-muted num">{a.doneTime}</td>
                <td className="col-num">
                  {hasResult(a) ? (a.evidenceTotal ?? a.evidence.length) : '—'}
                </td>
                <td>
                  {hasResult(a) ? (
                    <Link to={detail(a.id)}>분석 상세 →</Link>
                  ) : (
                    /* 결과가 없는 시도는 분석 상세가 아니라 실행 축이 답한다.
                     * 🔴 프로토타입은 여기서 `/ops/incidents` 로 보냈는데 **그 라우트가 없다** —
                     * `App.tsx` 의 와일드카드가 홈으로 되돌려, 운영자는 실행 대신 첫 화면을 본다.
                     * 규칙 엔진 화면(`/ops/*`)은 아직 안 들어왔다. 서버가 실패 런에 실어 보내는
                     * 안내와 같은 곳을 가리킨다("실행 상세는 파이프라인 실행 이력에서" —
                     * `AnalysisResponse.result`). 라우트가 생기면 그때 옮긴다. */
                    <Link to="/grid">파이프라인 실행 이력 →</Link>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );

  return preview ? <MockPreview>{body}</MockPreview> : body;
}
