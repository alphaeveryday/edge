import { useState } from 'react';
import { PageSkeleton, Select, StatusBadge, Toggle, toast } from 'ui-kit';
import type { ServeStatus } from '../domains/explanations';
import { CONFIDENCE_LABEL, STATUS_LABEL, STATUS_TONE } from '../domains/explanations';
import type { RuleType, WordAction } from '../domains/screening';
import {
  useBannedWords,
  useCriteria,
  useDisclaimer,
  usePolicyVersions,
  useRules,
  useScreeningActions,
} from '../domains/screening/hooks';
import { useSession } from '../domains/session/hooks';
import { LoadError } from './_shared/cells';

const ACTION_LABEL: Record<WordAction, string> = { REVIEW: '검수 필요', BLOCK: '점검 차단' };

type Tab = 'words' | 'rules' | 'disclaimer' | 'history';

export function ScreeningPage() {
  const [tab, setTab] = useState<Tab>('words');
  // 정책 변경(=새 버전 발행)은 CR 전용(permission-matrix) — 강제 지점은 API 필터이고,
  // 화면은 비CR 에게 쓰기 컨트롤을 감춰 403 조작 시도를 예방한다(UsersPage 선례).
  const { data: session } = useSession();
  const canEdit = session?.role === 'COMPLIANCE_REVIEWER';

  return (
    <div className="flex max-w-[900px] flex-col gap-5">
      <div className="tabs">
        <div className={`tab${tab === 'words' ? ' active' : ''}`} onClick={() => setTab('words')}>
          금칙어
        </div>
        <div className={`tab${tab === 'rules' ? ' active' : ''}`} onClick={() => setTab('rules')}>
          점검 처리 기준
        </div>
        <div className={`tab${tab === 'disclaimer' ? ' active' : ''}`} onClick={() => setTab('disclaimer')}>
          면책 문구
        </div>
        <div className={`tab${tab === 'history' ? ' active' : ''}`} onClick={() => setTab('history')}>
          버전 이력
        </div>
      </div>

      {tab === 'words' && <WordsTab canEdit={canEdit} />}
      {tab === 'rules' && <RulesTab canEdit={canEdit} onManageWords={() => setTab('words')} />}
      {tab === 'disclaimer' && <DisclaimerTab canEdit={canEdit} />}
      {tab === 'history' && <HistoryTab />}
    </div>
  );
}

function WordsTab({ canEdit }: { canEdit: boolean }) {
  const { data: words = [], isError, isPending } = useBannedWords();
  const { addWord, toggleWord } = useScreeningActions();

  const [text, setText] = useState('');
  const [action, setAction] = useState<WordAction>('BLOCK');

  const submit = () => {
    const t = text.trim();
    if (!t) {
      toast('등록할 표현을 입력하세요.');
      return;
    }
    addWord.mutate(
      { text: t, action },
      {
        onSuccess: () => {
          setText('');
          toast('금칙어가 등록되었습니다.');
        },
      },
    );
  };

  if (isError) return <LoadError />;
  // 로딩 중 빈 목록 오표시 방지
  if (isPending) return <PageSkeleton />;

  return (
    <div className="flex flex-col gap-4">
      {canEdit && (
      <div className="card card-pad">
        <div className="t-label mb-3">금칙어 등록</div>
        <div className="flex flex-wrap items-end gap-2">
          <div className="flex flex-col gap-1">
            <span style={{ fontSize: 11, color: 'var(--fg-3)' }}>표현</span>
            <label className="field w-[220px]">
              <input placeholder="예: 급등 확실" value={text} onChange={(e) => setText(e.target.value)} />
            </label>
          </div>
          <div className="flex flex-col gap-1">
            <span style={{ fontSize: 11, color: 'var(--fg-3)' }}>처리 방식</span>
            <Select
              aria-label="처리 방식"
              value={action}
              onChange={(v) => setAction(v as WordAction)}
              options={(Object.keys(ACTION_LABEL) as WordAction[]).map((a) => ({ value: a, label: ACTION_LABEL[a] }))}
            />
          </div>
          <button className="btn btn-primary" onClick={submit}>
            등록
          </button>
        </div>
      </div>
      )}

      <div className="card">
        <div className="card-head">
          <span className="t-label">금칙어 목록</span>
          <span className="num" style={{ fontSize: 11, color: 'var(--fg-4)' }}>
            활성 {words.filter((w) => w.active).length} / 전체 {words.length}
          </span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>표현</th>
              <th>처리 방식</th>
              <th>활성 여부</th>
              <th className="col-muted">등록일</th>
            </tr>
          </thead>
          <tbody>
            {words.map((w) => (
              <tr key={w.id} style={{ opacity: w.active ? 1 : 0.45 }}>
                <td className="font-semibold">“{w.text}”</td>
                {/* 처리 방식이 결과를 정하는 유일한 축이다 — 위험 등급은 판정에 쓰이지 않아
                    은퇴했다(ALPHA-760). 처리 기준 표에도 나타나지 않는다. */}
                <td className="col-muted">{ACTION_LABEL[w.action]}</td>
                <td>
                  {canEdit ? (
                    <Toggle on={w.active} onToggle={() => toggleWord.mutate(w.id)} aria-label={`${w.text} 활성 여부`} />
                  ) : (
                    <span className="col-muted">{w.active ? '활성' : '비활성'}</span>
                  )}
                </td>
                <td className="col-muted num">{w.registeredAt}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** 룰 타입 → 점검 항목 이름. 금칙어는 처리 방식별 요약 행이 따로 담당한다. */
const RULE_TYPE_ITEM: Record<Exclude<RuleType, 'BANNED_WORD'>, string> = {
  SINGLE_SOURCE: '단일 출처 룰',
  ASSERTIVE_EXPRESSION: '단정 표현 룰',
};

/** 걸렸을 때 도달하는 원장 상태 — 설명 목록·검수 큐가 쓰는 배지 그대로다(어휘도 톤도
 * STATUS_LABEL·STATUS_TONE 이 SSOT). 이 표만의 칩을 새로 만들면 같은 상태에 두 모양이 생긴다. */
function ResultBadge({ status }: { status: ServeStatus }) {
  return (
    <StatusBadge tone={STATUS_TONE[status]} dot={false}>
      {STATUS_LABEL[status]}
    </StatusBadge>
  );
}

/** 결과 없음 — 왜 없는지를 옆에 단다. 빈 칸만 두면 로딩 실패와 구분되지 않는다. */
function NoResult({ why }: { why: string }) {
  return (
    <span className="col-muted">
      — <span style={{ fontSize: 11 }}>{why}</span>
    </span>
  );
}

/**
 * 점검 처리 기준 — "무엇이 걸리면 어떻게 되는가"를 활성 정책에서 파생한 한 표다(ALPHA-756).
 * 이전 화면은 검수·차단 기준을 하드코딩 문구로 그려 설정과 어긋났다(확신도를 미설정해도
 * "확신도 기준 미달"이 남고, 등록 UI 가 없는 단정 표현이 기준처럼 보였다). 표의 모든 행은
 * criteria(policy_version 게이트)·rules(screening_rule 인스턴스)·엔진 고정 판정 중 하나에서
 * 나온다 — 정책에 없는 조건은 화면에도 없다.
 *
 * 세 열은 항목 이름 · 설정값 · 도달 상태다. 결과 어휘는 원장 상태 라벨(STATUS_LABEL)을 그대로
 * 쓴다 — 같은 상태를 화면마다 다른 이름으로 부르지 않는다. 설정값은 걸리는 쪽 극성이다
 * ("1개 이하면 걸림"): 표 전체가 "걸리면 자동 제공 제외"라 그쪽이 일관된다. 버전 이력 표는
 * 자동 제공 기준의 기록이라 반대 극성("2개 이상")을 그대로 둔다 — 통일하면 하나가 거짓이 된다.
 *
 * 게이트(충족해야 자동 제공)와 룰(걸리면 검수·차단)은 저장 계층에서 합치지 않는다 —
 * ADR-0046 이 폐기한 이중 반전이다. 여기선 같은 술어를 같은 형식으로 읽히게만 한다.
 */
function RulesTab({ canEdit, onManageWords }: { canEdit: boolean; onManageWords: () => void }) {
  const criteriaQuery = useCriteria();
  const rulesQuery = useRules();
  const { updateCriteria } = useScreeningActions();

  const changed = () => toast('자동 제공 기준이 변경되었습니다.');

  if (criteriaQuery.isError || rulesQuery.isError) return <LoadError />;
  // 로드 전 select 기본값(2/MEDIUM)이 실제 설정처럼 보이지 않게 — 로드 후 렌더
  if (criteriaQuery.isPending || rulesQuery.isPending) return <PageSkeleton />;

  const criteria = criteriaQuery.data;
  const rules = rulesQuery.data;
  const on = criteria.autoPublishEnabled;
  // 비활성 룰은 판정하지 않는다 — 요약에서 세면 걸리지 않는 조건을 걸린다고 말하게 된다.
  const activeWords = rules.filter((r) => r.ruleType === 'BANNED_WORD' && r.enabled);
  const blockWords = activeWords.filter((r) => r.action === 'BLOCK').length;
  const reviewWords = activeWords.length - blockWords;
  const otherRules = rules.filter((r) => r.ruleType !== 'BANNED_WORD');
  /**
   * 결과 칸. 항목이 비었으면(금칙어 0건·기준 미설정) 그 사유를 대고, 아니면 도달 상태를 낸다.
   * 스위치는 여기서 보지 않는다 — 룰(금칙어)과 UNCERTAIN 은 평가기에서 스위치보다 **먼저**
   * 판정되므로 스위치를 꺼도 그대로 적용된다(PolicyEvaluator 순서: 룰 → UNCERTAIN → 스위치
   * → 게이트). 스위치에 무력화되는 건 게이트 두 행뿐이라 그건 gateResult 가 따로 다룬다.
   */
  const result = (configured: boolean, status: ServeStatus, emptyWhy: string) =>
    configured ? <ResultBadge status={status} /> : <NoResult why={emptyWhy} />;

  /** 게이트 행(출처 수·확신도) — 스위치가 꺼져 있으면 평가기가 여기까지 오지 않는다. */
  const gateResult = (configured: boolean, emptyWhy: string) => {
    if (!configured) return <NoResult why={emptyWhy} />;
    return on ? <ResultBadge status="REVIEW_REQUIRED" /> : <NoResult why="자동 제공 꺼짐" />;
  };

  return (
    <div className="flex flex-col gap-3">
      {!criteria.published && (
        // 활성 정책이 없으면 판정기가 NEW 를 아예 집지 않는다(BundleScreener: 정책 부재 =
        // 진행 중단). 아래 값은 현재 정책이 아니라 첫 발행에 쓰일 기반값이라, 결론 행이
        // "자동 제공"이라 말하면 거짓이 된다.
        <div className="card card-pad" style={{ borderLeft: '2px solid var(--warn)', fontSize: 12 }}>
          <span className="chip chip-warn">발행 전</span>
          <span style={{ color: 'var(--fg-2)', marginLeft: 8 }}>
            아직 발행된 정책이 없어 설명 판정이 진행되지 않습니다. 아래는 첫 발행에 쓰일 기반값입니다 —
            무엇이든 변경하면 첫 버전이 발행됩니다.
          </span>
        </div>
      )}
      <div className="card">
      <div className="card-head">
        <span className="t-label">점검 처리 기준</span>
        {/* 스위치는 항목이 아니라 표 전체를 지배하는 값이라 행이 아니라 헤더에 둔다. */}
        <span className="flex items-center gap-2" style={{ fontSize: 12, color: 'var(--fg-2)' }}>
          자동 제공
          {canEdit ? (
            <Toggle
              on={on}
              onToggle={() => updateCriteria.mutate({ autoPublishEnabled: !on }, { onSuccess: changed })}
              aria-label="자동 제공 사용 여부"
            />
          ) : null}
          <span style={{ color: 'var(--fg-3)' }}>{on ? '사용' : '전건 검수'}</span>
        </span>
      </div>
      <div style={{ fontSize: 12, color: 'var(--fg-3)', padding: '10px 12px 0' }}>
        {on
          ? '항목에 하나라도 걸리면 자동 제공되지 않습니다.'
          : '스위치가 꺼져 있어 어디에도 걸리지 않은 설명까지 검수 대기열로 갑니다 — 금칙어·원인 미확인은 그대로 적용됩니다.'}
      </div>
      {/* 열 폭을 고정한다 — auto-layout 이면 결과 칸 내용이 스위치 상태에 따라 배지(짧다)와
          "— 자동 제공 꺼짐"(길다)로 바뀌면서 설정 열까지 밀어낸다. 상태 전환이 레이아웃
          전환으로 보이면 안 된다(ALPHA-764). `.table` 은 ui-kit 공유라 이 표에만 건다. */}
      <table className="table" style={{ tableLayout: 'fixed' }}>
        <colgroup>
          <col />
          <col style={{ width: 200 }} />
          <col style={{ width: 200 }} />
        </colgroup>
        <thead>
          <tr>
            <th>점검 항목</th>
            <th>설정</th>
            <th>결과</th>
          </tr>
        </thead>
        <tbody>
          {/* 결과가 센 순 — 차단이 위, 검수가 가운데, 자동 제공이 결론 행으로 맨 아래. */}
          <tr>
            <td className="font-semibold">차단 금칙어</td>
            <td>
              <button className="btn w-[140px] justify-center" onClick={onManageWords}>
                금칙어 관리
              </button>
            </td>
            <td>{result(blockWords > 0, 'BLOCKED', '활성 0건')}</td>
          </tr>
          <tr>
            <td className="font-semibold">검수 금칙어</td>
            <td>
              <button className="btn w-[140px] justify-center" onClick={onManageWords}>
                금칙어 관리
              </button>
            </td>
            <td>{result(reviewWords > 0, 'REVIEW_REQUIRED', '활성 0건')}</td>
          </tr>
          <tr>
            <td className="font-semibold">출처 수</td>
            <td>
              {/* 설정 컨트롤 폭은 전부 140px 로 맞춘다 — 드롭다운과 버튼의 총폭이 같아야
                  세로선이 선다(Select 는 트리거 상자에 box-sizing: border-box 를 건다). */}
              <Select
                aria-label="출처 수"
                width={140}
                disabled={!canEdit}
                placeholder="기준 없음"
                value={criteria.minSources == null ? '' : String(criteria.minSources)}
                onChange={(v) =>
                  updateCriteria.mutate({ minSources: Number(v) as 1 | 2 | 3 }, { onSuccess: changed })
                }
                options={[
                  { value: '1', label: '출처 없음' },
                  { value: '2', label: '1개 이하' },
                  { value: '3', label: '2개 이하' },
                ]}
              />
            </td>
            <td>{gateResult(criteria.minSources != null, '기준 미설정')}</td>
          </tr>
          <tr>
            <td className="font-semibold">확신도</td>
            <td>
              {/* 미설정(NULL)=게이트 꺼짐은 placeholder 로만 보이고 선택 불가다 — 설정은
                  단방향(해제 어휘 없음, ALPHA-634 의 발행 모델 결정). 어휘가 MEDIUM·HIGH 뿐인
                  것은 DB CHECK 이고, LOW 는 미설정과 실질 동일이라 애초에 빠져 있다. */}
              <Select
                aria-label="확신도"
                width={140}
                disabled={!canEdit}
                placeholder="기준 없음"
                value={criteria.minConfidence ?? ''}
                onChange={(v) =>
                  updateCriteria.mutate({ minConfidence: v as 'MEDIUM' | 'HIGH' }, { onSuccess: changed })
                }
                options={[
                  { value: 'MEDIUM', label: '보류 이하' },
                  { value: 'HIGH', label: '중간 이하' },
                ]}
              />
            </td>
            <td>
              {gateResult(criteria.minConfidence != null, '기준 미설정')}
            </td>
          </tr>
          <tr>
            {/* 엔진이 스스로 원인 미확인으로 판정한 설명은 정책 설정과 무관하게 항상 검수다
                (PolicyEvaluator — ADR-0046 의 "모호성은 전부 검수" 결정). 확신도와 별개 축이라
                한 행을 유지한다 — 지금 엔진이 둘을 함께 내보내 겹쳐 보일 뿐이다(ALPHA-759). */}
            <td className="font-semibold">원인 미확인</td>
            <td className="col-muted">항상 검수 · 확신도 무관</td>
            <td>{result(true, 'REVIEW_REQUIRED', '')}</td>
          </tr>
          {/* 금칙어 밖 룰(단일 출처·단정 표현)은 인스턴스가 있을 때만 행이 된다 — 콘솔에
              만들 경로가 없어 현재는 0건이고, 없는 것을 기준처럼 그리지 않는다. */}
          {otherRules.map((rule) => (
            <tr key={rule.id} style={{ opacity: rule.enabled ? 1 : 0.45 }}>
              <td className="font-semibold">
                {RULE_TYPE_ITEM[rule.ruleType as Exclude<RuleType, 'BANNED_WORD'>]}
              </td>
              <td className="col-muted">
                {rule.enabled ? '활성 · 고정' : '비활성 · 고정'}
                {rule.text && ` · ‘${rule.text}’`}
                {/* 텍스트 매칭 타입인데 params.text 가 없으면 판정기가 예외로 멈춘다
                    (PolicyEvaluator.match — 계약 위반). 조용히 정상처럼 그리지 않는다. */}
                {rule.ruleType === 'ASSERTIVE_EXPRESSION' && !rule.text && (
                  <span style={{ color: 'var(--down)' }}> · 표현 없음 — 판정 불가</span>
                )}
              </td>
              <td>
                {result(rule.enabled, rule.action === 'BLOCK' ? 'BLOCKED' : 'REVIEW_REQUIRED', '비활성')}
              </td>
            </tr>
          ))}
          <tr>
            <td className="col-muted">어느 항목에도 걸리지 않음</td>
            <td className="col-muted">—</td>
            <td>
              {!criteria.published ? (
                <NoResult why="발행 전" />
              ) : (
                <ResultBadge status={on ? 'AUTO_PUBLISHED' : 'REVIEW_REQUIRED'} />
              )}
            </td>
          </tr>
        </tbody>
      </table>
      <div style={{ fontSize: 11, color: 'var(--fg-3)', padding: '10px 12px' }}>
        {STATUS_LABEL.BLOCKED}은 검수 대기열에 뜨지 않습니다 — 필요하면 설명 상세에서 검수로 이관할 수 있습니다.
        확신도 기준을 두면 확신도가 <b>미산정</b>인 설명도 함께 걸립니다(정보가 없으면 미달로 봅니다).
        한 설명이 여러 항목에 동시에 걸리면 {STATUS_LABEL.BLOCKED}이 {STATUS_LABEL.REVIEW_REQUIRED}보다 우선합니다.
      </div>
      </div>
    </div>
  );
}


function DisclaimerTab({ canEdit }: { canEdit: boolean }) {
  const { data: saved, isError, isPending } = useDisclaimer();
  const { updateDisclaimer } = useScreeningActions();
  const [draft, setDraft] = useState<string>();

  if (isError) return <LoadError />;
  // 저장값 로드 전에 저장하면 빈 값으로 면책 문구를 덮어쓴다 — 로드 후 렌더
  if (isPending) return <PageSkeleton />;

  const text = draft ?? saved;

  return (
    <div className="card max-w-[720px]">
      <div className="card-head">
        <span className="t-label">면책 문구</span>
        <span className="num" style={{ fontSize: 11, color: 'var(--fg-4)' }}>
          {text.length}자
        </span>
      </div>
      <div className="flex flex-col gap-3 p-4">
        <div style={{ fontSize: 12, color: 'var(--fg-3)', lineHeight: 1.6 }}>
          모든 가격 변동 설명 하단에 자동으로 표기됩니다. 투자 권유로 오인되지 않도록 관계 법령에 맞게 작성하세요.
        </div>
        <textarea className="textarea" rows={4} value={text} readOnly={!canEdit} onChange={(e) => setDraft(e.target.value)} />
        <div
          className="rounded-[5px] p-3"
          style={{ border: '1px dashed var(--border-strong)', background: 'var(--bg-sunken)' }}
        >
          <div className="t-label mb-1.5">제공 미리보기</div>
          <div style={{ fontSize: 11, color: 'var(--fg-3)', lineHeight: 1.6 }}>{text}</div>
        </div>
        {canEdit && (
          <div className="flex justify-end">
            <button
              className="btn btn-primary"
              onClick={() =>
                updateDisclaimer.mutate(text, { onSuccess: () => toast('면책 문구가 저장되었습니다.') })
              }
            >
              저장
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/** 이력의 min_confidence 원값 → 설정 화면과 같은 표시 문구. 정책 어휘는 MEDIUM|HIGH 뿐이라
 * (DB CHECK·ALPHA-634) 확신도 배지 어휘(LOW 포함)로 감싸지 않는다 — LOW 가 이력에 있으면
 * 그건 드리프트지 정상 정책이 아니고, "보류 이상"으로 렌더하면 감사 이력에서 정상처럼 보인다.
 * 어휘 밖 값은 원값 그대로 노출한다(Rule 12). */
function confidenceText(minConfidence: string | null): string {
  if (minConfidence == null) return '—';
  if (minConfidence !== 'MEDIUM' && minConfidence !== 'HIGH') return minConfidence;
  return `${CONFIDENCE_LABEL[minConfidence]} 이상`;
}

function HistoryTab() {
  const { data: versions = [], isError, isPending } = usePolicyVersions();

  if (isError) return <LoadError />;
  // 로딩 중 빈 이력 오표시 방지
  if (isPending) return <PageSkeleton />;

  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">정책 버전 이력</span>
      </div>
      {/* 정책은 불변 버전(ADR-0018) — 모든 변경이 새 버전 발행이라 이력이 곧 감사 추적이다. */}
      <table className="table">
        <thead>
          <tr>
            <th>버전</th>
            <th>발행 시각</th>
            <th>발행자</th>
            <th>자동 제공</th>
            <th>출처 수</th>
            <th>확신도</th>
            <th>상태</th>
          </tr>
        </thead>
        <tbody>
          {versions.map((v) => (
            <tr key={v.versionNo}>
              <td className="num">v{v.versionNo}</td>
              <td className="col-muted num">{v.publishedAt ? new Date(v.publishedAt).toLocaleString('sv-SE').slice(0, 16) : '—'}</td>
              <td>{v.publishedBy ?? '—'}</td>
              <td>{v.autoPublishEnabled ? '사용' : '전건 검수'}</td>
              {/* 이력 셀도 설정 화면과 같은 어휘로 — 헤더가 축만 말하므로 조건은 값이 진다.
                  순수 숫자가 아니게 되어 num(우측 정렬)은 뗀다(확신도 열과 같은 좌측 정렬). */}
              <td>{v.minSources != null ? `${v.minSources}개 이상` : '—'}</td>
              <td>{confidenceText(v.minConfidence)}</td>
              <td>{v.active ? <span className="chip">활성</span> : <span className="col-muted">종결</span>}</td>
            </tr>
          ))}
          {versions.length === 0 && (
            <tr>
              <td colSpan={7} className="col-muted">
                아직 발행된 정책 버전이 없습니다 — 금칙어·기준·문구를 변경하면 첫 버전이 발행됩니다.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
