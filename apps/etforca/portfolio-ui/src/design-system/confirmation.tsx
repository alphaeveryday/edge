import { useEffect, useId, useRef, useState } from 'react';
import { Button } from './components';

export function useConfirmation() {
  const [question, setQuestion] = useState<{ message: string; action: string } | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const resolve = useRef<((answer: boolean) => void) | null>(null);
  const launcher = useRef<HTMLElement | null>(null);
  const titleId = useId();
  useEffect(() => { if (question && !dialog.current?.open) dialog.current?.showModal(); }, [question]);
  useEffect(() => () => { resolve.current?.(false); resolve.current = null; }, []);
  function restoreFocus(fallback?: HTMLElement | null) {
    const target = launcher.current;
    launcher.current = null;
    requestAnimationFrame(() => {
      if (target?.isConnected) target.focus();
      else if (target && document.activeElement === document.body) fallback?.focus();
    });
  }
  function finish(answer: boolean) {
    const pending = resolve.current;
    resolve.current = null;
    dialog.current?.close();
    setQuestion(null);
    pending?.(answer);
    if (!answer) restoreFocus();
  }
  function ask(message: string, action = '삭제하기', returnFocus?: HTMLElement | null) {
    if (resolve.current) return Promise.resolve(false);
    launcher.current = returnFocus ?? document.activeElement as HTMLElement;
    return new Promise<boolean>(done => { resolve.current = done; setQuestion({ message, action }); });
  }
  const confirmation = question && <dialog ref={dialog} className="ds-confirm-dialog" aria-labelledby={titleId} onCancel={e => { e.preventDefault(); finish(false); }}>
    <h2 id={titleId}>{question.message}</h2><div className="ds-confirm-actions"><Button type="button" variant="secondary" autoFocus onClick={() => finish(false)}>취소</Button><Button type="button" variant="primary" onClick={() => finish(true)}>{question.action}</Button></div>
  </dialog>;
  return { ask, confirmation, restoreFocus };
}
