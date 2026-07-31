/* tenants 도메인 — 페이지가 사용하는 hook. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { tenantsRepository } from './index';
import type { NewTenant } from './types';

const KEY = ['tenants'];

export function useTenants() {
  return useQuery({ queryKey: KEY, queryFn: () => tenantsRepository.list() });
}

export function useTenant(id: string | undefined) {
  const query = useTenants();
  return { ...query, tenant: query.data?.find((t) => t.id === id) };
}

export function useCreateTenant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: NewTenant) => tenantsRepository.create(input),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
    // 실패는 생성 모달이 인라인(fError)으로 표면화한다 — 전역 토스트와 중복 금지.
    meta: { suppressGlobalToast: true },
  });
}
