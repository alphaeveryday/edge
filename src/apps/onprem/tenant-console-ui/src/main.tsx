import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'ui-kit';
import { App } from './App';
// tailwind(preflight) → ui-kit 순서 고정: preflight가 토큰·컴포넌트 스타일을 덮지 않게
import './styles/app.css';
import 'ui-kit/styles.css';

const root = document.getElementById('root');
if (!root) throw new Error('#root 엘리먼트를 찾을 수 없습니다');

// mutation 실패는 전역에서 토스트로 드러낸다 (Rule 12 — 조용한 실패 금지)
const queryClient = new QueryClient({
  mutationCache: new MutationCache({
    onError: (error) => {
      toast(error instanceof Error ? error.message : '요청이 실패했습니다.');
    },
  }),
});

// dev 한정 세션 쿠키 확보 후 렌더 — 첫 쿼리들이 401 로 헛돌지 않게 한다. 조건이
// 정적(import.meta.env.DEV)이라 prod 번들에서는 분기·동적 import 청크가 통째로
// 제거된다 — 데모 자격증명이 배포 번들에 실리지 않는다. 실패해도 화면은 띄운다
// (각 쿼리의 에러 표면이 원인을 드러낸다 — 조용한 공백 화면 금지).
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
