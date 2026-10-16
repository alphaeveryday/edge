import { useEffect, useState } from 'react';
import { api } from './api';
import { CustomerReport } from './CustomerReport';
import { Readable } from './Detail';

type Run = { id: string; version: string; environment: string; created_at: string };
type Case = { case: number; title: string; input: Record<string, unknown>; expected: string };
type Attempt = { case_id: number; attempt: number; state: string; failures: string[]; seconds: number | null; interrupted?: boolean };
type RunDetail = Run & { cases: Case[]; attempts: Attempt[] };
type Answer = Attempt & { input: Record<string, unknown>; result: Record<string, unknown> | null };
const status = (a?: Attempt) => !a ? '미실행' : a.interrupted ? '중단 의심 · 완료 기록 없음' : a.state === 'running' ? '실행 중' : a.state === 'failed' ? '실행 실패' : a.failures.length ? '자동검사 실패' : '자동검사 통과';

export function Verification({ onDisconnect, refresh }: { onDisconnect: () => void; refresh: number }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [runId, setRunId] = useState('');
  const [run, setRun] = useState<RunDetail | null>(null);
  const [selection, setSelection] = useState<{ caseId: number; attempt: number } | null>(null);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const rows = await api<Run[]>('/verification-runs');
        const chosen = runId || rows[0]?.id;
        const detail = chosen ? await api<RunDetail>(`/verification-runs/${encodeURIComponent(chosen)}`) : null;
        const output = chosen && selection ? await api<Answer>(`/verification-runs/${encodeURIComponent(chosen)}/${selection.caseId}/${selection.attempt}`) : null;
        if (!stopped) { setRuns(rows); setRun(detail); setAnswer(output); setLoaded(true); if (!runId && chosen) setRunId(chosen); timer = setTimeout(poll, 10000); }
      } catch { if (!stopped) onDisconnect(); }
    }
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [runId, selection, refresh]);
  function choose(id: string) { setRunId(id); setRun(null); setSelection(null); setAnswer(null); }
  return <section className="verification" aria-label="버전별 20개 유형 검증">
    <h2>버전별 20개 유형 검증</h2>
    <label htmlFor="verification-version">검증 버전</label>{' '}
    <select id="verification-version" value={run?.id || runId} onChange={e => choose(e.target.value)}>
      {runs.map(r => <option key={r.id} value={r.id}>{r.version} · {r.environment === 'production_runtime' ? '운영 런타임' : '로컬 검증'} · 등록 {new Date(r.created_at).toLocaleString('ko-KR')}</option>)}
    </select>
    {!loaded ? <p role="status">검증 이력을 불러오고 있어요.</p> : !runs.length ? <p>등록된 검증 이력이 없습니다.</p> : !run ? <p role="status">선택한 버전을 불러오고 있어요.</p> : <>
      <p>{run.cases.length}개 유형</p>
      <div className="table-wrap"><table><thead><tr><th>유형</th><th>최근 결과</th><th>실행 이력</th></tr></thead><tbody>{run.cases.map(c => {
        const attempts = run.attempts.filter(a => a.case_id === c.case);
        return <tr key={c.case}><td>{c.case}. {c.title}</td><td>{status(attempts.at(-1))}</td><td>{attempts.length ? attempts.map(a => <button className="quiet" key={a.attempt} onClick={() => { setAnswer(null); setSelection({ caseId: c.case, attempt: a.attempt }); }}>{c.case}번 · {a.attempt}차 · {status(a)}</button>) : '아직 실행하지 않음'}</td></tr>;
      })}</tbody></table></div>
      {selection && <section aria-label="검증 입력과 답변"><h3>{run.cases.find(c => c.case === selection.caseId)?.title} · {selection.attempt}차</h3>
        <p>기대 결과: {run.cases.find(c => c.case === selection.caseId)?.expected}</p>
        {answer && <details><summary>실행 당시 입력 전체</summary><Readable value={answer.input} /></details>}
        {!answer ? <p role="status">답변을 불러오고 있어요.</p> : <><p>{status(answer)}{answer.seconds !== null ? ` · ${Math.round(answer.seconds)}초` : ''}</p>
          {!!answer.failures.length && <ul>{answer.failures.map((f, i) => <li key={i}>{f}</li>)}</ul>}
          {answer.result ? <CustomerReport result={answer.result} /> : <p>저장된 답변이 없습니다.</p>}
        </>}
      </section>}
    </>}
  </section>;
}
