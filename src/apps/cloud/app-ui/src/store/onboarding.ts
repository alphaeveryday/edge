import { create } from 'zustand';

interface OnboardingState {
  themes: string[];
  etfs: string[];
  toggleTheme: (k: string) => void;
  toggleEtf: (code: string) => void;
}

const toggle = (arr: string[], k: string) => (arr.includes(k) ? arr.filter((x) => x !== k) : [...arr, k]);

export const useOnboarding = create<OnboardingState>((set) => ({
  themes: [],
  etfs: [],
  toggleTheme: (k) => set((s) => ({ themes: toggle(s.themes, k) })),
  toggleEtf: (c) => set((s) => ({ etfs: toggle(s.etfs, c) })),
}));
