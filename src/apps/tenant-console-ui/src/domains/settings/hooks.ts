/* settings 도메인 — 페이지가 사용하는 hook.
 * 페이지는 repository 를 직접 다루지 않고 이 hook 만 쓴다.
 */
import { useCallback, useEffect, useState } from 'react';
import { settingsRepository } from './index';
import type { OrgProfile, PolicyPreset, NotificationSettings } from './types';

export interface UseSettingsResult {
  org: OrgProfile | null;
  presets: PolicyPreset[];
  notifications: NotificationSettings | null;
  loading: boolean;
  error: Error | null;
  /** 알림 한 항목을 갱신하고 결과를 즉시 반영한다. */
  setNotification: (key: keyof NotificationSettings, value: boolean) => Promise<void>;
}

export function useSettings(): UseSettingsResult {
  const [org, setOrg] = useState<OrgProfile | null>(null);
  const [presets, setPresets] = useState<PolicyPreset[]>([]);
  const [notifications, setNotifications] = useState<NotificationSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    Promise.all([
      settingsRepository.getOrg(),
      settingsRepository.listPresets(),
      settingsRepository.getNotifications(),
    ])
      .then(([o, p, n]) => {
        if (!active) return;
        setOrg(o);
        setPresets(p);
        setNotifications(n);
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

  const setNotification = useCallback(
    async (key: keyof NotificationSettings, value: boolean) => {
      const next = await settingsRepository.updateNotifications({ [key]: value });
      setNotifications(next);
    },
    [],
  );

  return { org, presets, notifications, loading, error, setNotification };
}
