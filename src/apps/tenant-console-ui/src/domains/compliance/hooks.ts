/* compliance 도메인 — 페이지가 사용하는 hook.
 * 페이지는 repository 를 직접 다루지 않고 이 hook 만 쓴다.
 */
import { useCallback, useEffect, useState } from 'react';
import { complianceRepository } from './index';
import type { DisclaimerDoc, Keyword } from './types';

export interface UseComplianceResult {
  disclaimer: DisclaimerDoc | null;
  keywords: Keyword[];
  blockedToday: number;
  loading: boolean;
  error: Error | null;
  /** 면책 문구 발행 후 즉시 반영하고 갱신된 문서를 반환한다. */
  publish: (text: string) => Promise<DisclaimerDoc>;
  /** 차단 단어 추가 후 목록을 즉시 반영한다. */
  addKeyword: (word: string) => Promise<Keyword[]>;
  /** 차단 단어 제거 후 목록을 즉시 반영한다. */
  removeKeyword: (word: string) => Promise<Keyword[]>;
}

export function useCompliance(): UseComplianceResult {
  const [disclaimer, setDisclaimer] = useState<DisclaimerDoc | null>(null);
  const [keywords, setKeywords] = useState<Keyword[]>([]);
  const [blockedToday, setBlockedToday] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    Promise.all([
      complianceRepository.getDisclaimer(),
      complianceRepository.listKeywords(),
      complianceRepository.getBlockedToday(),
    ])
      .then(([d, k, b]) => {
        if (!active) return;
        setDisclaimer(d);
        setKeywords(k);
        setBlockedToday(b);
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

  const publish = useCallback(async (text: string) => {
    const updated = await complianceRepository.publishDisclaimer(text);
    setDisclaimer(updated);
    return updated;
  }, []);

  const addKeyword = useCallback(async (word: string) => {
    const next = await complianceRepository.addKeyword(word);
    setKeywords(next);
    return next;
  }, []);

  const removeKeyword = useCallback(async (word: string) => {
    const next = await complianceRepository.removeKeyword(word);
    setKeywords(next);
    return next;
  }, []);

  return { disclaimer, keywords, blockedToday, loading, error, publish, addKeyword, removeKeyword };
}
