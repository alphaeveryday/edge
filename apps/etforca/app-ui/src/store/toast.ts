import { create } from 'zustand';

export type ToastKind = 'ok' | 'error';

interface ToastState {
  text: string | null;
  kind: ToastKind;
  show: (text: string, kind?: ToastKind) => void;
  hide: () => void;
}

let timer: ReturnType<typeof setTimeout> | undefined;

export const useToast = create<ToastState>((set) => ({
  text: null,
  kind: 'ok',
  show: (text, kind = 'ok') => {
    if (timer) clearTimeout(timer);
    set({ text, kind });
    timer = setTimeout(() => set({ text: null }), 1800);
  },
  hide: () => set({ text: null }),
}));
