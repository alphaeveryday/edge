import { useRouter } from 'expo-router';
import { create } from 'zustand';
import { api } from '@/api';
import { onboarding, tokens } from '@/api/http/storage';

interface SessionState {
  restored: boolean;
  onboarded: boolean;
  loggedIn: boolean;
  finishOnboarding: () => void;
  login: () => void;
  logout: () => void;
  expire: () => void;
  restore: () => Promise<void>;
}

export const useSession = create<SessionState>((set) => ({
  restored: false,
  onboarded: false,
  loggedIn: false,
  finishOnboarding: () => { onboarding.save(true); set({ onboarded: true }); },
  login: () => set({ loggedIn: true }),
  logout: () => { onboarding.save(false); set({ loggedIn: false, onboarded: false }); },
  // 서버의 토큰 거부 시 온보딩 상태를 둔 로그아웃
  expire: () => set({ loggedIn: false }),
  // 기동 시 온보딩 완료와 회원 확인 기반 로그인 상태 복원
  restore: async () => {
    const onboarded = await onboarding.done();
    let loggedIn = false;
    if (process.env.EXPO_PUBLIC_API_MODE === 'http' && (await tokens.access())) {
      try {
        await api.member.me();
        loggedIn = true;
      } catch {
        await tokens.clear();
      }
    }
    set({ restored: true, onboarded, loggedIn });
  },
}));

// 비로그인 시 동작 대신 로그인 화면으로 보내는 래퍼. 이유는 로그인 화면의 안내 한 줄, before 는 이동 전 모달 닫기용
export const useRequireLogin = () => {
  const loggedIn = useSession((s) => s.loggedIn);
  const router = useRouter();
  return (reason: string, fn: () => void, before?: () => void) => {
    if (loggedIn) return fn();
    before?.();
    router.push({ pathname: '/login', params: { reason } });
  };
};
