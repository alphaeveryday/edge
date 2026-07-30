import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'ui-kit';
import { App } from './App';
import { ApiError } from './api/client';
// tailwind(preflight) → ui-kit 순서 고정: preflight가 토큰·컴포넌트 스타일을 덮지 않게
import './styles/app.css';
import 'ui-kit/styles.css';

const root = document.getElementById('root');
if (!root) throw new Error('#root 엘리먼트를 찾을 수 없습니다');

// 인증 배선(ALPHA-626): 어떤 쿼리/뮤테이션이든 401 을 만나면 세션 쿼리를 무효화해
// RequireSession 가드가 /login 으로 보내게 한다(사용 중 만료 포함). 세션 쿼리 자신의
// 401 에 또 무효화하면 refetch 무한 루프라 키를 제외한다.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 401/403 은 재시도해도 결과가 같다 — 기본 3회 백오프(~7초)만큼 로그인 진입이 늦어진다.
      retry: (failureCount, error) =>
        !(error instanceof ApiError && (error.status === 401 || error.status === 403)) && failureCount < 3,
    },
  },
  queryCache: new QueryCache({
    onError: (error, query) => {
      if (error instanceof ApiError && error.status === 401 && query.queryKey[0] !== 'session') {
        queryClient.invalidateQueries({ queryKey: ['session'] });
      }
    },
  }),
  // mutation 실패는 전역에서 토스트로 드러낸다 (Rule 12 — 조용한 실패 금지).
  // 401 은 로그인 화면의 만료 배너가 설명하므로 토스트 대신 세션 무효화만 하고,
  // 자체 에러 표면이 있는 뮤테이션(로그인 등)은 meta.suppressGlobalToast 로 중복을 끈다.
  mutationCache: new MutationCache({
    onError: (error, _variables, _onMutateResult, mutation) => {
      if (error instanceof ApiError && error.status === 401) {
        queryClient.invalidateQueries({ queryKey: ['session'] });
        return;
      }
      if (mutation.meta?.suppressGlobalToast) return;
      toast(error instanceof Error ? error.message : '요청이 실패했습니다.');
    },
  }),
});

// 세션 쿠키 확보 후 렌더 — 첫 쿼리들이 401 로 헛돌지 않게 한다(콘솔 API 는 fail-closed).
// vite dev 전용 자동 세션 브릿지 — 서빙 빌드(데모 박스 포함)는 조건이 정적 false 라 분기·
// 동적 import 청크가 통째로 제거돼 자격증명이 번들에 실리지 않고, 로그인 화면(ALPHA-626)이
// 유일한 진입이다. 데모 박스 빌드의 VITE_DEMO_AUTOSESSION 경로는 콘솔 CloudFront 공개
// (ALPHA-627)와 함께 폐기했다 — 공개 표면에 무인증 관리자 세션을 둘 수 없다.
// 실패해도 화면은 띄운다(가드가 /login 으로 보내 원인을 드러낸다 — 조용한 공백 화면 금지).
async function bootstrapDevSession(): Promise<void> {
  if (!import.meta.env.DEV) return;
  const { ensureDevSession } = await import('./api/devSession');
  await ensureDevSession();
}

bootstrapDevSession()
  .catch((error) => console.error('dev 세션 부트스트랩 실패:', error))
  .finally(() => {
    createRoot(root).render(
      <StrictMode>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </QueryClientProvider>
      </StrictMode>,
    );
  });
