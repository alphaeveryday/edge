import { create } from 'zustand';
import { api } from '@/api';
import { tokens } from '@/api/http/storage';

interface SessionState {
  onboarded: boolean;
  loggedIn: boolean;
  gateOpen: boolean;
  gateReason: string;
  finishOnboarding: () => void;
  login: () => void;
  logout: () => void;
  expire: () => void;
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
  // 토큰이 서버에서 거부된 뒤. 온보딩 상태는 유지
  expire: () => set({ loggedIn: false }),
  // 기동 시 보관된 토큰으로 /me 가 통하면 로그인 상태 복원. 거부되면 토큰 폐기
  restore: async () => {
    if (process.env.EXPO_PUBLIC_API_MODE !== 'http') return;
    if (!(await tokens.access())) return;
    try {
      await api.user.me();
      set({ loggedIn: true });
    } catch {
      await tokens.clear();
    }
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
