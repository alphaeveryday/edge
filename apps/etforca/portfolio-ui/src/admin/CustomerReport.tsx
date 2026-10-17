import { useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ReportView } from '../ReportView';
import type { Report } from '../api';
import baseStyles from '../styles.css?inline';
import tokens from '../design-system/tokens.css?inline';
import components from '../design-system/components.css?inline';

// Keep customer resets, variables and body:has(#report) rules out of the dashboard.
const styles = tokens.replaceAll(':root', ':host') + baseStyles + components + `
  :host { display:block; min-width:0; }
  .report-surface { background:var(--color-canvas); color:var(--t1); font-family:var(--font);
    font-size:16px; line-height:1.55; word-break:keep-all; overflow-wrap:anywhere;
    border-radius:16px; padding:20px 12px; }
  #report { max-width:760px; margin:auto; padding-bottom:0; }
  @media(max-width:700px) { .report-surface { padding:12px 4px; } }
`;

export function CustomerReport({ result }: { result: Record<string, unknown> }) {
  const host = useRef<HTMLDivElement>(null);
  const [root, setRoot] = useState<ShadowRoot | null>(null);
  useLayoutEffect(() => { setRoot(host.current!.shadowRoot ?? host.current!.attachShadow({ mode: 'open' })); }, []);
  return <div ref={host}>{root && createPortal(<><style>{styles}</style><div className="report-surface"><ReportView report={result as Report} separateEvidence allSections /></div></>, root)}</div>;
}
