/* applications 도메인 — 페이지가 사용하는 hook.
 * 페이지는 repository 를 직접 다루지 않고 이 hook 만 쓴다.
 */
import { useCallback, useEffect, useState } from 'react';
import { applicationsRepository } from './index';
import type {
  Application,
  WidgetKey,
  Webhook,
  CreateAppInput,
  IssueKeyInput,
  AddWebhookInput,
} from './types';

export interface UseApplicationsResult {
  apps: Application[];
  loading: boolean;
  error: Error | null;
  /** 앱 생성 후 목록에 즉시 반영하고 생성된 앱을 반환한다. */
  createApp: (input: CreateAppInput) => Promise<Application>;
}

export function useApplications(): UseApplicationsResult {
  const [apps, setApps] = useState<Application[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    applicationsRepository
      .listApps()
      .then((a) => {
        if (active) setApps(a);
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

  const createApp = useCallback(async (input: CreateAppInput) => {
    const created = await applicationsRepository.createApp(input);
    setApps((prev) => [...prev, created]);
    return created;
  }, []);

  return { apps, loading, error, createApp };
}

export interface UseApplicationResult {
  app: Application | null;
  loading: boolean;
  error: Error | null;
}

export function useApplication(slug: string | undefined): UseApplicationResult {
  const [app, setApp] = useState<Application | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!slug) {
      setApp(null);
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    setError(null);
    applicationsRepository
      .getApp(slug)
      .then((a) => {
        if (active) setApp(a);
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
  }, [slug]);

  return { app, loading, error };
}

export interface UseWidgetKeysResult {
  keys: WidgetKey[];
  loading: boolean;
  error: Error | null;
  issueKey: (input: IssueKeyInput) => Promise<WidgetKey>;
  regenerateKey: (key: string) => Promise<WidgetKey>;
  revokeKey: (key: string) => Promise<void>;
}

export function useWidgetKeys(): UseWidgetKeysResult {
  const [keys, setKeys] = useState<WidgetKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    applicationsRepository
      .listKeys()
      .then((k) => {
        if (active) setKeys(k);
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

  const issueKey = useCallback(async (input: IssueKeyInput) => {
    const created = await applicationsRepository.issueKey(input);
    setKeys((prev) => [...prev, created]);
    return created;
  }, []);

  const regenerateKey = useCallback(async (key: string) => {
    const next = await applicationsRepository.regenerateKey(key);
    setKeys((prev) => prev.map((k) => (k.key === key ? next : k)));
    return next;
  }, []);

  const revokeKey = useCallback(async (key: string) => {
    await applicationsRepository.revokeKey(key);
    setKeys((prev) => prev.filter((k) => k.key !== key));
  }, []);

  return { keys, loading, error, issueKey, regenerateKey, revokeKey };
}

export interface UseWebhooksResult {
  webhooks: Webhook[];
  loading: boolean;
  error: Error | null;
  addWebhook: (input: AddWebhookInput) => Promise<Webhook>;
}

export function useWebhooks(): UseWebhooksResult {
  const [webhooks, setWebhooks] = useState<Webhook[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    applicationsRepository
      .listWebhooks()
      .then((w) => {
        if (active) setWebhooks(w);
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

  const addWebhook = useCallback(async (input: AddWebhookInput) => {
    const created = await applicationsRepository.addWebhook(input);
    setWebhooks((prev) => [...prev, created]);
    return created;
  }, []);

  return { webhooks, loading, error, addWebhook };
}

export interface UseUniverseResult {
  universe: string[];
  loading: boolean;
  error: Error | null;
}

export function useUniverse(): UseUniverseResult {
  const [universe, setUniverse] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    applicationsRepository
      .getUniverse()
      .then((u) => {
        if (active) setUniverse(u);
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

  return { universe, loading, error };
}
