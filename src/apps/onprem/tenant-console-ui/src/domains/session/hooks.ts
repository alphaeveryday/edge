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
