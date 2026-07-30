/* session 도메인 — 페이지가 사용하는 hook. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { sessionRepository } from './index';

const KEY = ['session'];

export function useSession() {
  // staleTime: 가드(RequireSession)와 ConsoleLayout 이 연속 구독한다 — 0 이면 마운트마다
  // 이중 조회되고, 두 번째 요청의 일시 실패가 화면 전체를 가린다. 세션 변경은 로그인
  // (removeQueries)·프로필 수정(invalidate)이 명시적으로 무효화하므로 신선도 손실 없음.
  return useQuery({ queryKey: KEY, queryFn: () => sessionRepository.current(), staleTime: 60_000 });
}

export function useUpdateDisplayName() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => sessionRepository.updateDisplayName(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { email: string; password: string }) => sessionRepository.login(v.email, v.password),
    // 실패는 로그인 화면이 인라인·배너로 전부 표면화한다 — 전역 토스트 중복 억제.
    meta: { suppressGlobalToast: true },
    // invalidate 가 아니라 remove: /login 에는 세션 쿼리 관찰자가 없어 invalidate 는 stale
    // 마킹만 하고, 캐시에 남은 직전 401 에러를 가드가 읽어 도로 /login 으로 튕긴다.
    onSuccess: () => qc.removeQueries({ queryKey: KEY }),
  });
}

export function useLogout() {
  // 착지는 호출부(ConsoleLayout)가 /login 이동 후 캐시 clear 로 처리한다.
  return useMutation({ mutationFn: () => sessionRepository.logout() });
}
