/* 설명·검수 상세 공통 컴포넌트(ALPHA-922) — 골격·정보 구성은 설명 상세 기준(카드형
 * 헤더·유형 chip·출처 카운트·원본 문구 카드), 시각 표기·빈 상태·뒤로가기 규율은 검수
 * 상세 기준(KST 명시·빈 상태 문구·ghost 버튼). 두 화면이 같은 정보를 다른 모양으로
 * 그리지 않게 여기서만 그린다. */
import { Fragment, useEffect, useRef, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Icon } from 'ui-kit';
import { isHttpUrl } from './links';
import type { EvidenceAudit } from '../../lib/evidence';
import 'katex/dist/katex.min.css';

/** 뒤로가기 — ghost 버튼(명확한 클릭 타깃) + arrowLeft 한 가지로 통일. */
export function BackLink({ to, label }: { to: string; label: string }) {
  return (
    <div>
      <Link to={to} className="btn btn-sm btn-ghost">
        <Icon name="arrowLeft" className="ic" /> {label}
      </Link>
    </div>
  );
}

/** 카드형 상세 헤더 — 종목 아이덴티티 + 라벨 필드 그리드 + 우측 액션 슬롯. */
export function DetailHeader({
  name,
  code,
  sub,
  fields,
  actions,
}: {
  name: string;
  code: string;
  sub?: ReactNode;
  fields: { label: string; value: ReactNode }[];
  actions?: ReactNode;
}) {
  return (
    <div className="card card-pad flex flex-wrap items-center gap-6">
      <div className="min-w-[180px]">
        <div style={{ fontSize: 18, fontWeight: 700 }}>
          {name}{' '}
          <span className="num" style={{ fontSize: 13, fontWeight: 400, color: 'var(--fg-4)' }}>
            {code}
          </span>
        </div>
        {sub && (
          <div style={{ fontSize: 12, color: 'var(--fg-3)', marginTop: 2 }}>{sub}</div>
        )}
      </div>
      <div className="flex items-center gap-8">
        {fields.map((f) => (
          <div key={f.label}>
            <div className="t-label">{f.label}</div>
            <div className="mt-1.5 flex items-center gap-1.5">{f.value}</div>
          </div>
        ))}
      </div>
      <div className="flex-1" />
      {actions && <div className="flex gap-2">{actions}</div>}
    </div>
  );
}

/** 근거 한 행 — 유형·시각은 표시 문자열로 받는다(번역·포맷은 각 도메인 소관). */
export interface EvidenceRow extends EvidenceAudit {
  type: string;
  title: string;
  source: string;
  time: string;
  sourceUri?: string | null;
}

/** 근거 데이터 카드 — 출처 카운트·유형 chip·원문 링크·빈 상태까지 한 벌. */
export function EvidenceTable({ rows }: { rows: EvidenceRow[] }) {
  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">근거 데이터</span>
        <span className="num" style={{ fontSize: 11, color: 'var(--fg-4)' }}>
          {rows.length}개 근거
        </span>
      </div>
      <table className="table">
        <thead>
          <tr>
            <th>유형</th>
            <th>내용</th>
            <th>출처</th>
            <th className="col-muted">시각</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((ev, i) => (
            <Fragment key={i}>
              <tr>
                <td>
                  <span className="chip">{ev.type}</span>
                </td>
                <td>
                  {/* 원문 링크(ALPHA-739) — 결측(EOD 구멍 등)·비웹 URI 는 일반 텍스트 폴백 */}
                  {ev.sourceUri && isHttpUrl(ev.sourceUri) ? (
                    <a href={ev.sourceUri} target="_blank" rel="noopener noreferrer">
                      {ev.title}
                    </a>
                  ) : (
                    ev.title
                  )}
                  {ev.newsId && <div className="t-data mt-1" style={{ color: 'var(--fg-3)' }}>뉴스 ID · {ev.newsId}</div>}
                </td>
                <td className="col-muted">{ev.source}</td>
                <td className="col-muted t-data">{ev.time}</td>
              </tr>
              {ev.type === '수치 계산' && (
                <tr>
                  <td colSpan={4} style={{ whiteSpace: 'normal', padding: 20 }}>
                    <CalculationEvidence evidence={ev} />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && (
        <div className="p-6 text-center" style={{ color: 'var(--fg-3)', fontSize: 12 }}>
          근거 데이터가 없습니다.
        </div>
      )}
    </div>
  );
}

function Formula({ latex }: { latex: string }) {
  const element = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let active = true;
    import('katex').then(({ default: katex }) => {
      if (active && element.current) katex.render(latex, element.current, {
        throwOnError: false, trust: false, maxSize: 20, maxExpand: 1000,
      });
    }).catch(() => {
      if (active && element.current) element.current.textContent = `수식 표시 실패 · ${latex}`;
    });
    return () => { active = false; };
  }, [latex]);
  return <div ref={element} aria-label={`수식: ${latex}`} className="py-3" />;
}

function CalculationEvidence({ evidence: e }: { evidence: EvidenceRow }) {
  return (
    <div className="flex flex-col gap-3" style={{ overflowWrap: 'anywhere' }}>
      <div>
        <div className="t-label">툴 설명</div>
        <p className="mt-1" style={{ lineHeight: 1.65 }}>{e.description || '저장된 설명이 없습니다.'}</p>
      </div>
      <div>
        <div className="t-label">수식</div>
        {e.formulaLatex ? <Formula latex={e.formulaLatex} /> : <p className="mt-1">저장된 수식이 없습니다.</p>}
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {[{ label: '입력', value: e.arguments }, { label: '결과', value: e.output }].map(({ label, value }) => (
          <div key={label} className="min-w-0">
            <div className="t-label">{label}</div>
            <pre className="mt-2 rounded-lg p-3" style={{
              whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: 12,
              lineHeight: 1.65, background: 'var(--bg-sunken)', color: 'var(--fg-2)',
            }}>{value == null ? '저장된 값이 없습니다.' : JSON.stringify(value, null, 2)}</pre>
          </div>
        ))}
      </div>
      <div className="t-data" style={{ color: 'var(--fg-3)' }}>툴 실행 ID · {e.toolRunId || '없음'}</div>
    </div>
  );
}

/** 원본 설명 문구 카드 — 줄바꿈 보존(ALPHA-913)·"모델 생성" 표기 한 벌. */
export function OriginalSummaryCard({ text }: { text: string }) {
  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">원본 설명 문구</span>
        <span className="chip">모델 생성</span>
      </div>
      <div
        className="p-4 whitespace-pre-line"
        style={{ fontSize: 13, lineHeight: 1.65, color: 'var(--fg-2)' }}
      >
        {text}
      </div>
    </div>
  );
}
