import { create } from 'zustand';

interface SessionState {
  onboarded: boolean;
  loggedIn: boolean;
  finishOnboarding: () => void;
  login: () => void;
  logout: () => void;
}

export const useSession = create<SessionState>((set) => ({
  onboarded: false,
  loggedIn: false,
  finishOnboarding: () => set({ onboarded: true }),
  login: () => set({ loggedIn: true }),
  logout: () => set({ loggedIn: false }),
}));
