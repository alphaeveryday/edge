/* dashboard 도메인 — 페이지가 사용하는 hook.
 * 페이지는 repository 를 직접 다루지 않고 이 hook 만 쓴다. */
import { useQuery } from '@tanstack/react-query';
import { dashboardRepository } from './index';

export function useTrafficSummary() {
  return useQuery({ queryKey: ['dashboard-traffic'], queryFn: () => dashboardRepository.traffic() });
}
