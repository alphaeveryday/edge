import { router, useRouter } from 'expo-router';
import { create } from 'zustand';
import { api, isApiError } from '@/api';
import { queryClient } from '@/lib/queryClient';
import { useWatchGroup } from './watch';
import { useOnboarding } from './onboarding';
import { guestDisclaimer, onboarding, tokens } from '@/api/http/storage';

interface SessionState {
  restored: boolean;
  onboarded: boolean;
  loggedIn: boolean;
  revision: number;
  guestDisclaimed: boolean;
  acceptGuestDisclaimer: () => void;
  finishOnboarding: () => void;
  login: () => void;
  logout: (restart: boolean) => void;
  expire: () => Promise<void>;
  restore: () => Promise<void>;
}

export const useSession = create<SessionState>((set, get) => ({
  restored: false,
  onboarded: false,
  loggedIn: false,
  revision: 0,
  guestDisclaimed: false,
  acceptGuestDisclaimer: () => { guestDisclaimer.save(); set({ guestDisclaimed: true }); },
  finishOnboarding: () => { onboarding.save(true); set({ onboarded: true }); },
  // HTTP 로그인은 토큰 교체 알림에서 처리하며 mock 로그인도 같은 캐시 정리를 거친다.
  login: () => { if (!get().loggedIn) changeSession(true); },
  // 탈퇴만 온보딩 재시작
  logout: (restart) => {
    if (get().loggedIn) changeSession(false);
    if (!restart) return;
    onboarding.save(false);
    set({ loggedIn: false, onboarded: false });
  },
  // 서버의 토큰 거부 시 온보딩 상태를 둔 로그아웃
  expire: () => tokens.clear('expired'),
  // 기동 시 기기 상태와 회원 확인 기반의 로그인 상태 복원
  restore: async () => {
    const version = tokens.version();
    const onboarded = await onboarding.done();
    const guestDisclaimed = await guestDisclaimer.done();
    let loggedIn = false;
    if (process.env.EXPO_PUBLIC_API_MODE === 'http' && (await tokens.access())) {
      // 토큰 거부만 로그아웃, 오프라인 기동은 로그인 유지
      try {
        await api.member.me();
        loggedIn = true;
      } catch (e) {
        loggedIn = !isApiError(e, 'UNAUTHORIZED');
      }
    }
    // 복원 중 로그인·만료가 일어났다면 그 전환의 회원 상태를 보존한다.
    set({ restored: true, onboarded, guestDisclaimed, ...(version === tokens.version() ? { loggedIn } : {}) });
  },
}));

// 토큰의 주체가 바뀌는 순간 조회 취소·초기화와 화면의 회원 상태를 함께 바꾼다.
function changeSession(loggedIn: boolean) {
  // 새 그룹의 placeholder가 이전 query를 참조하기 전에 원본 데이터를 비운다.
  void queryClient.resetQueries();
  useSession.setState({ loggedIn, revision: tokens.version() });
  useWatchGroup.getState().setGroup('base');
  useOnboarding.getState().reset();
}

tokens.subscribe(async (change, version) => {
  // 이미 resolve된 조회의 setData까지 끝난 뒤 초기화해야 이전 응답이 다시 저장되지 않는다.
  await queryClient.cancelQueries();
  tokens.assertCurrent(version);
  const wasLoggedIn = useSession.getState().loggedIn;
  changeSession(change === 'login');
  if (change === 'expired' && wasLoggedIn) router.push({ pathname: '/login', params: { reason: '만료' } });
});

// 비로그인 시 동작 대신 로그인 화면으로 보내는 래퍼
// reason 은 로그인 화면의 안내 한 줄
// before 는 이동 전 모달 닫기용
export const useRequireLogin = () => {
  const loggedIn = useSession((s) => s.loggedIn);
  const router = useRouter();
  return (reason: string, fn: () => void, before?: () => void) => {
    if (loggedIn) return fn();
    before?.();
    router.push({ pathname: '/login', params: { reason } });
  };
};
