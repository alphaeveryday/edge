import { create } from 'zustand';
import { tokens } from '@/api/http/storage';

interface SessionState {
  onboarded: boolean;
  loggedIn: boolean;
  gateOpen: boolean;
  gateReason: string;
  finishOnboarding: () => void;
  login: () => void;
  logout: () => void;
  restore: () => Promise<void>;
  openGate: (reason: string) => void;
  closeGate: () => void;
}

export const useSession = create<SessionState>((set) => ({
  onboarded: false,
  loggedIn: false,
  gateOpen: false,
  gateReason: '',
  finishOnboarding: () => set({ onboarded: true }),
  login: () => set({ loggedIn: true, gateOpen: false }),
  logout: () => set({ loggedIn: false, onboarded: false }),
  // 기동 시 보관된 액세스 토큰이 있으면 로그인 상태로 복원. mock 모드는 토큰이 없으니 그대로
  restore: async () => {
    if (process.env.EXPO_PUBLIC_API_MODE !== 'http') return;
    if (await tokens.access()) set({ loggedIn: true });
  },
  openGate: (gateReason) => set({ gateOpen: true, gateReason }),
  closeGate: () => set({ gateOpen: false }),
}));

// 로그인이 필요한 동작을 감싼다. 비로그인이면 유도 시트를 띄우고 동작은 실행하지 않는다
export const useRequireLogin = () => {
  const loggedIn = useSession((s) => s.loggedIn);
  const openGate = useSession((s) => s.openGate);
  return (reason: string, fn: () => void) => (loggedIn ? fn() : openGate(reason));
};
