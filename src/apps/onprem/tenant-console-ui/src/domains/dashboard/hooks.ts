/* dashboard 도메인 — 페이지가 사용하는 hook.
 * 페이지는 repository 를 직접 다루지 않고 이 hook 만 쓴다.
 */
import { useEffect, useState } from 'react';
import { dashboardRepository } from './index';
import type { DashboardOverview } from './types';

export interface UseDashboardResult {
  overview: DashboardOverview | null;
  loading: boolean;
  error: Error | null;
}

export function useDashboard(): UseDashboardResult {
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    dashboardRepository
      .getOverview()
      .then((o) => {
        if (active) setOverview(o);
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

  return { overview, loading, error };
}
