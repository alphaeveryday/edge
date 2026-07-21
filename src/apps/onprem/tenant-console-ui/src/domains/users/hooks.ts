/* users 도메인 — 페이지가 사용하는 hook. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { usersRepository } from './index';
import type { MemberRole } from './types';

const KEY = ['members'];

export function useMembers() {
  return useQuery({ queryKey: KEY, queryFn: () => usersRepository.list() });
}

export function useInviteMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ email, role }: { email: string; role: MemberRole }) =>
      usersRepository.invite(email, role),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}
