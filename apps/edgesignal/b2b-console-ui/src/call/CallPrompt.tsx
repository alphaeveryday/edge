import { Delta } from 'ui-kit';
import { direction, type QueuedReport } from '../report.ts';
import { SectorLogo } from '../components/SectorLogo';
import type { Sentence } from './history.ts';

/** 콘솔 위에 뜨는 "○○로 이동할까요?" 탭. 버전 2 는 그 종목에 대한 최근 추천 답변을 함께 보여 준다. */
export function CallPrompt(p: { report: QueuedReport; ver: 1 | 2; answer: Sentence | null; onGo: () => void; onDismiss: () => void }) {
  const r = p.report;
  return (
    <div className="call-prompt" role="dialog" aria-label={`${r.name}로 이동할까요?`}>
      <div className="pr-etf">
        <SectorLogo theme={r.theme} size={44} />
        <div className="pr-name">
          <b>{r.name}</b>
          <span>{r.code} · 통화에서 들렸어요</span>
        </div>
        <Delta direction={direction(r.pct)} pct={Math.abs(r.pct)} style={{ fontSize: 20 }} />
      </div>
      <div className="pr-q">{r.name}로 이동할까요?</div>
      {p.ver === 2 && (
        <div className="say">
          <div className="say-head">고객에게 이렇게 말해 보세요</div>
          <p>
            {p.answer?.answer || <span className="say-wait">고객 질문이 끝나면 답변을 만들어요</span>}
            {p.answer?.answering && <span className="caret" />}
          </p>
        </div>
      )}
      <div className="pr-actions">
        <button type="button" className="pr-close" onClick={p.onDismiss}>
          이번 통화 동안 닫기
        </button>
        <button type="button" className="pr-go" onClick={p.onGo}>
          이동
        </button>
      </div>
    </div>
  );
}
