/* 세션 가드 — 인증 필수 표면(AdminLayout 전체)을 감싸는 pathless 라우트.
 * 401 이면 /login 으로 보내되, 에러 시점에 세션 data 가 남아 있으면(= 한 번 인증됐다가
 * 만료) reason:'expired' 를 실어 로그인 화면이 만료 배너를 띄우게 한다 — react-query v5 는
 * refetch 실패 시 이전 data 를 지우지 않으므로 이 판별로 충분하다(별도 플래그 불필요). */
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { ApiError } from '../api/client';
import { useSession } from '../domains/session/hooks';
import { LoadError } from '../pages/_shared/LoadError';

export function RequireSession() {
  const location = useLocation();
  const { data, error, isPending } = useSession();

  if (isPending) return null;
  if (error instanceof ApiError && error.status === 401) {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: location, reason: data !== undefined ? 'expired' : undefined }}
      />
    );
  }
  if (error) return <LoadError error={error} />;
  return <Outlet />;
}
