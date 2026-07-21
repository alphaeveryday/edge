import { useState } from 'react';
import { StatusBadge, Toggle, toast } from 'ui-kit';
import type { RiskLevel } from '../domains/explanations';
import { RISK_LABEL, RISK_TONE } from '../domains/explanations';
import type { WordAction } from '../domains/screening';
import { useBannedWords, useCriteria, useDisclaimer, useScreeningActions } from '../domains/screening/hooks';
import { LoadError } from './_shared/cells';

const ACTION_LABEL: Record<WordAction, string> = { REVIEW: '검수 필요', BLOCK: '점검 차단' };

type Tab = 'words' | 'rules' | 'disclaimer';

export function ScreeningPage() {
  const [tab, setTab] = useState<Tab>('words');

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
      </div>

      {tab === 'words' && <WordsTab />}
      {tab === 'rules' && <RulesTab />}
      {tab === 'disclaimer' && <DisclaimerTab />}
    </div>
  );
}

function WordsTab() {
  const { data: words = [], isError } = useBannedWords();
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

  return (
    <div className="flex flex-col gap-4">
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
                  <Toggle on={w.active} onToggle={() => toggleWord.mutate(w.id)} aria-label={`${w.text} 활성 여부`} />
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

function RulesTab() {
  const { data: criteria, isError } = useCriteria();
  const { updateCriteria } = useScreeningActions();

  const changed = () => toast('자동 제공 기준이 변경되었습니다.');

  if (isError) return <LoadError />;

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
            <span style={{ color: 'var(--fg-2)' }}>허용 위험 등급</span>
            <select
              className="select"
              style={{ height: 26, fontSize: 11 }}
              value={criteria?.maxRisk ?? 'MEDIUM'}
              onChange={(e) =>
                updateCriteria.mutate({ maxRisk: e.target.value as 'LOW' | 'MEDIUM' }, { onSuccess: changed })
              }
            >
              <option value="LOW">저위험만</option>
              <option value="MEDIUM">중위험까지</option>
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
          {['단일 출처 기반 설명', '단정 표현 감지', '고위험 등급 판정'].map((label) => (
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

function DisclaimerTab() {
  const { data: saved, isError } = useDisclaimer();
  const { updateDisclaimer } = useScreeningActions();
  const [draft, setDraft] = useState<string>();

  const text = draft ?? saved ?? '';

  if (isError) return <LoadError />;

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
        <textarea className="textarea" rows={4} value={text} onChange={(e) => setDraft(e.target.value)} />
        <div
          className="rounded-[5px] p-3"
          style={{ border: '1px dashed var(--border-strong)', background: 'var(--bg-sunken)' }}
        >
          <div className="t-label mb-1.5">제공 미리보기</div>
          <div style={{ fontSize: 11, color: 'var(--fg-3)', lineHeight: 1.6 }}>{text}</div>
        </div>
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
      </div>
    </div>
  );
}
