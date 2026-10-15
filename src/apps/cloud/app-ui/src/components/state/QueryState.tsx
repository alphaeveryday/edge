import type { ReactNode } from 'react';
import type { UseQueryResult } from '@tanstack/react-query';
import { isApiError } from '@/api';
import { ErrorView } from './ErrorView';
import { Loading } from './Loading';
import { Pending } from './Pending';

interface Props<T> {
  query: UseQueryResult<T>;
  rows?: number;
  pending?: { title?: string; sub?: string };
  children: (data: T) => ReactNode;
}

// 조회 상태별 화면 분기
export function QueryState<T>({ query, rows, pending, children }: Props<T>) {
  if (query.isPending) return <Loading rows={rows} />;
  if (query.isError) {
    if (isApiError(query.error, 'NOT_READY')) return <Pending {...pending} />;
    return <ErrorView onRetry={() => query.refetch()} />;
  }
  return <>{children(query.data)}</>;
}
