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
// 로그인 화면(ALPHA-626)과 공존하는 자동 세션 브릿지다. 두 빌드에서만 켠다:
//   - dev(import.meta.env.DEV): 로컬 개발.
//   - 데모 박스 서빙 빌드(VITE_DEMO_AUTOSESSION='true', Dockerfile 이 설정): SSM 터널 데모.
// 두 조건 모두 정적이라, 실 온프렘 빌드(플래그 없음)에서는 분기·동적 import 청크가 통째로
// 제거돼 데모 자격증명이 번들에 실리지 않는다 — 그 빌드는 로그인 화면이 유일한 진입이다.
// 실패해도 화면은 띄운다(가드가 /login 으로 보내 원인을 드러낸다 — 조용한 공백 화면 금지).
async function bootstrapDevSession(): Promise<void> {
  if (!import.meta.env.DEV && import.meta.env.VITE_DEMO_AUTOSESSION !== 'true') return;
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
