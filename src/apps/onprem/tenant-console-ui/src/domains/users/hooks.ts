/* users 도메인 — 페이지가 사용하는 hook. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { usersRepository } from './index';
import type { RegisterMemberInput } from './repository';
import type { MemberRole } from './types';

const KEY = ['members'];

/** enabled=false 이면 조회를 보내지 않는다 — 비관리자에게 403 을 유발하지 않기 위함. */
export function useMembers(enabled = true) {
  return useQuery({ queryKey: KEY, queryFn: () => usersRepository.list(), enabled });
}

export function useRegisterMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: RegisterMemberInput) => usersRepository.register(input),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useDeactivateMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => usersRepository.deactivate(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useChangeRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, role }: { id: number; role: MemberRole }) =>
      usersRepository.changeRole(id, role),
    // 실패(경쟁 변경 409 등)에도 invalidate — 다른 관리자가 방금 바꾼 역할이 stale 로
    // 남지 않게 성공·실패 모두 목록을 재조회한다.
    onSettled: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}
