/* members 도메인 — 페이지가 사용하는 hook.
 * 페이지는 repository 를 직접 다루지 않고 이 hook 만 쓴다.
 */
import { useCallback, useEffect, useState } from 'react';
import { membersRepository } from './index';
import type { Member, RoleSummary, InviteMemberInput } from './types';

export interface UseMembersResult {
  members: Member[];
  roles: RoleSummary[];
  loading: boolean;
  error: Error | null;
  /** 구성원 초대 후 목록에 즉시 반영하고 생성된 구성원을 반환한다. */
  invite: (input: InviteMemberInput) => Promise<Member>;
}

export function useMembers(): UseMembersResult {
  const [members, setMembers] = useState<Member[]>([]);
  const [roles, setRoles] = useState<RoleSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    Promise.all([membersRepository.list(), membersRepository.listRoles()])
      .then(([m, r]) => {
        if (!active) return;
        setMembers(m);
        setRoles(r);
      })
      .catch((e: unknown) => {
        if (active) setError(e instanceof Error ? e : new Error(String(e)));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const invite = useCallback(async (input: InviteMemberInput) => {
    const created = await membersRepository.invite(input);
    setMembers((prev) => [...prev, created]);
    return created;
  }, []);

  return { members, roles, loading, error, invite };
}
