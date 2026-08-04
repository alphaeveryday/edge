import { useState } from 'react';
import { PageSkeleton, StatusBadge, Toggle, toast } from 'ui-kit';
import type { RiskLevel } from '../domains/explanations';
import { RISK_LABEL, RISK_TONE } from '../domains/explanations';
import type { WordAction } from '../domains/screening';
import { useBannedWords, useCriteria, useDisclaimer, usePolicyVersions, useScreeningActions } from '../domains/screening/hooks';
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
      {tab === 'rules' && <RulesTab canEdit={canEdit} />}
      {tab === 'disclaimer' && <DisclaimerTab canEdit={canEdit} />}
      {tab === 'history' && <HistoryTab />}
    </div>
  );
}

function WordsTab({ canEdit }: { canEdit: boolean }) {
  const { data: words = [], isError, isPending } = useBannedWords();
  const { addWord, toggleWord } = useScreeningActions();

  const [text, setText] = useState('');
  const [risk, setRisk] = useState<RiskLevel>('HIGH');
  const [action, setAction] = useState<WordAction>('BLOCK');

  const submit = () => {
    const t = text.trim();
    if (!t) {
      toast('등록할 표현을 입력하세요.');
      return;
    }
    addWord.mutate(
      { text: t, risk, action },
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
            <span style={{ fontSize: 11, color: 'var(--fg-3)' }}>위험 등급</span>
            <select className="select" value={risk} onChange={(e) => setRisk(e.target.value as RiskLevel)}>
              {(Object.keys(RISK_LABEL) as RiskLevel[]).map((r) => (
                <option key={r} value={r}>
                  {RISK_LABEL[r]}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <span style={{ fontSize: 11, color: 'var(--fg-3)' }}>처리 방식</span>
            <select className="select" value={action} onChange={(e) => setAction(e.target.value as WordAction)}>
              <option value="REVIEW">검수 필요</option>
              <option value="BLOCK">점검 차단</option>
            </select>
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
              <th>위험 등급</th>
              <th>처리 방식</th>
              <th>활성 여부</th>
              <th className="col-muted">등록일</th>
            </tr>
          </thead>
          <tbody>
            {words.map((w) => (
              <tr key={w.id} style={{ opacity: w.active ? 1 : 0.45 }}>
                <td className="font-semibold">“{w.text}”</td>
                <td>
                  <StatusBadge tone={RISK_TONE[w.risk]} dot={false}>
                    {RISK_LABEL[w.risk]}
                  </StatusBadge>
                </td>
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

function RulesTab({ canEdit }: { canEdit: boolean }) {
  const { data: criteria, isError, isPending } = useCriteria();
  const { updateCriteria } = useScreeningActions();

  const changed = () => toast('자동 제공 기준이 변경되었습니다.');

  if (isError) return <LoadError />;
  // 로드 전 select 기본값(2/MEDIUM)이 실제 설정처럼 보이지 않게 — 로드 후 렌더
  if (isPending) return <PageSkeleton />;

  return (
    <div className="grid grid-cols-3 gap-3">
      <div className="card card-pad" style={{ borderTop: '2px solid var(--up)' }}>
        <div className="flex items-center gap-1.5">
          <span className="dot dot-up" />
          <span className="t-label" style={{ color: 'var(--fg-1)' }}>자동 제공 기준</span>
        </div>
        <div style={{ fontSize: 12, color: 'var(--fg-2)', margin: '10px 0 14px', lineHeight: 1.6 }}>
          아래 조건을 모두 충족하면 검수 없이 즉시 제공됩니다.
        </div>
        <div className="flex flex-col gap-2.5" style={{ fontSize: 12 }}>
          <div className="flex items-center justify-between gap-2">
            <span style={{ color: 'var(--fg-2)' }}>최소 출처 수</span>
            <select
              className="select"
              style={{ height: 26, fontSize: 11 }}
              disabled={!canEdit}
              value={criteria?.minSources ?? 2}
              onChange={(e) =>
                updateCriteria.mutate({ minSources: Number(e.target.value) as 1 | 2 | 3 }, { onSuccess: changed })
              }
            >
              <option value={1}>1개 이상</option>
              <option value={2}>2개 이상</option>
              <option value={3}>3개 이상</option>
            </select>
          </div>
          <div className="flex items-center justify-between gap-2">
            <span style={{ color: 'var(--fg-2)' }}>최소 확신도</span>
            <select
              className="select"
              style={{ height: 26, fontSize: 11 }}
              disabled={!canEdit}
              /* null(미설정=게이트 꺼짐)을 기본값으로 꾸미지 않는다 — 화면이 켜진 것처럼
                 보이면 보류 확신도가 자동 노출되는 동안 운영자가 모른다. 설정은 단방향
                 (해제 어휘 없음 — 발행 모델 YAGNI 결정)이라 미설정 옵션은 선택 불가다. */
              value={criteria?.minConfidence ?? ''}
              onChange={(e) =>
                updateCriteria.mutate({ minConfidence: e.target.value as 'MEDIUM' | 'HIGH' }, { onSuccess: changed })
              }
            >
              <option value="" disabled>미설정 (게이트 꺼짐)</option>
              <option value="MEDIUM">중간 이상</option>
              <option value="HIGH">높음만</option>
            </select>
          </div>
          <div className="flex items-center justify-between">
            <span style={{ color: 'var(--fg-2)' }}>활성 금칙어 미포함</span>
            <span className="chip">필수</span>
          </div>
        </div>
      </div>

      <div className="card card-pad" style={{ borderTop: '2px solid var(--warn)' }}>
        <div className="flex items-center gap-1.5">
          <span className="dot dot-warn" />
          <span className="t-label" style={{ color: 'var(--fg-1)' }}>검수 필요 기준</span>
        </div>
        <div style={{ fontSize: 12, color: 'var(--fg-2)', margin: '10px 0 14px', lineHeight: 1.6 }}>
          하나라도 해당하면 검수 대기열로 이동합니다.
        </div>
        <div className="flex flex-col gap-2.5" style={{ fontSize: 12 }}>
          {['단일 출처 기반 설명', '단정 표현 감지', '원인 미확인(UNCERTAIN) 판정', '확신도 기준 미달'].map((label) => (
            <div key={label} className="flex items-center justify-between">
              <span style={{ color: 'var(--fg-2)' }}>{label}</span>
              <span className="chip chip-warn">검수</span>
            </div>
          ))}
        </div>
      </div>

      <div className="card card-pad" style={{ borderTop: '2px solid var(--down)' }}>
        <div className="flex items-center gap-1.5">
          <span className="dot dot-down" />
          <span className="t-label" style={{ color: 'var(--fg-1)' }}>점검 차단 기준</span>
        </div>
        <div style={{ fontSize: 12, color: 'var(--fg-2)', margin: '10px 0 14px', lineHeight: 1.6 }}>
          하나라도 해당하면 제공이 자동 차단됩니다.
        </div>
        <div className="flex flex-col gap-2.5" style={{ fontSize: 12 }}>
          {['처리 방식 ‘점검 차단’ 금칙어 포함', '리딩·매수 추천 표현', '근거 데이터 없음'].map((label) => (
            <div key={label} className="flex items-center justify-between">
              <span style={{ color: 'var(--fg-2)' }}>{label}</span>
              <span className="chip chip-down">점검 차단</span>
            </div>
          ))}
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
            <th>최소 출처</th>
            <th>최소 확신도</th>
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
              <td className="num">{v.minSources ?? '—'}</td>
              <td>{v.minConfidence ?? '—'}</td>
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
