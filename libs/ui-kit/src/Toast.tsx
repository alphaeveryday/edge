import { useSyncExternalStore } from 'react';

/**
 * 토스트 — 하단 중앙 단일 메시지, 2.6초 후 자동 소멸 (시안 규약).
 * Provider 없이 모듈 스토어로 동작한다: 어디서든 toast("...") 호출,
 * 앱 루트에 <Toaster />를 한 번 둔다.
 */
let current = '';
let timer: ReturnType<typeof setTimeout> | undefined;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((l) => l());
}

export function toast(message: string) {
  clearTimeout(timer);
  current = message;
  emit();
  timer = setTimeout(() => {
    current = '';
    emit();
  }, 2600);
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function Toaster() {
  const message = useSyncExternalStore(subscribe, () => current);
  if (!message) return null;
  return <div className="toast">{message}</div>;
}
