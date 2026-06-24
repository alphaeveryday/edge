/* usage 도메인 — 페이지가 사용하는 hook.
 * 페이지는 repository 를 직접 다루지 않고 이 hook 만 쓴다.
 */
import { useEffect, useState } from 'react';
import { usageRepository } from './index';
import type { UsageReport } from './types';

export interface UseUsageResult {
  report: UsageReport | null;
  loading: boolean;
  error: Error | null;
}

export function useUsage(): UseUsageResult {
  const [report, setReport] = useState<UsageReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    usageRepository
      .getUsage()
      .then((r) => {
        if (active) setReport(r);
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

  return { report, loading, error };
}
