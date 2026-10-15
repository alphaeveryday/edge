import { create } from 'zustand';

interface OnboardingState {
  etfs: string[];
  toggleEtf: (code: string) => void;
  reset: () => void;
}

const toggle = (arr: string[], k: string) => (arr.includes(k) ? arr.filter((x) => x !== k) : [...arr, k]);

export const useOnboarding = create<OnboardingState>((set) => ({
  etfs: [],
  toggleEtf: (c) => set((s) => ({ etfs: toggle(s.etfs, c) })),
  reset: () => set({ etfs: [] }),
}));
