/* session 도메인 — 페이지가 사용하는 hook. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { sessionRepository } from './index';

const KEY = ['session'];

export function useSession() {
  return useQuery({ queryKey: KEY, queryFn: () => sessionRepository.current() });
}

export function useUpdateDisplayName() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => sessionRepository.updateDisplayName(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useLogout() {
  return useMutation({
    mutationFn: () => sessionRepository.logout(),
    onSuccess: () => {
      // 세션 무효화 후 전체 리로드 — 로그인 화면(ALPHA-486 범위 밖)이 없어 착지
      // 화면이 없다. dev 는 재진입 시 devSession 이 새 세션을 만든다. 실패는 전역
      // mutation 토스트가 드러낸다.
      window.location.reload();
    },
  });
}
