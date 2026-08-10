/* 설명·검수 상세 공통 컴포넌트(ALPHA-922) — 골격·정보 구성은 설명 상세 기준(카드형
 * 헤더·유형 chip·출처 카운트·원본 문구 카드), 시각 표기·빈 상태·뒤로가기 규율은 검수
 * 상세 기준(KST 명시·빈 상태 문구·ghost 버튼). 두 화면이 같은 정보를 다른 모양으로
 * 그리지 않게 여기서만 그린다. */
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Icon } from 'ui-kit';
import { isHttpUrl } from './links';

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
export interface EvidenceRow {
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
          {rows.length}개 출처
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
            <tr key={i}>
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
              </td>
              <td className="col-muted">{ev.source}</td>
              <td className="col-muted t-data">{ev.time}</td>
            </tr>
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
