import type { Query } from '@tanstack/react-query';
import { useCallback } from 'react';
import { useSession } from '@/store/session';

// 그룹 전환의 이전 내용은 같은 세션 안에서만 표시한다.
export const useSessionQuery = () => {
  const revision = useSession((s) => s.revision);
  const placeholderData = useCallback(<T,>(previous: T | undefined, query: Pick<Query, 'meta'> | undefined) =>
    query?.meta?.sessionRevision === revision ? previous : undefined, [revision]);
  return { meta: { sessionRevision: revision }, placeholderData };
};
