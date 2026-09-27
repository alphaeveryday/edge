import { create } from 'zustand';

interface ToastState {
  text: string | null;
  show: (text: string) => void;
  hide: () => void;
}

let timer: ReturnType<typeof setTimeout> | undefined;

export const useToast = create<ToastState>((set) => ({
  text: null,
  show: (text) => {
    if (timer) clearTimeout(timer);
    set({ text });
    timer = setTimeout(() => set({ text: null }), 1800);
  },
  hide: () => set({ text: null }),
}));
