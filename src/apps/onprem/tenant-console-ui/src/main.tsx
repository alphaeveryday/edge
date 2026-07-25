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

// 세션 쿠키 확보 후 렌더 — 첫 쿼리들이 401 로 헛돌지 않게 한다(콘솔 API 는 fail-closed).
// 로그인 화면(ALPHA-486/423) 도입 전까지의 임시 브릿지다. 두 빌드에서만 켠다:
//   - dev(import.meta.env.DEV): 로컬 개발.
//   - 데모 박스 서빙 빌드(VITE_DEMO_AUTOSESSION='true', Dockerfile 이 설정): SSM 터널 데모.
// 두 조건 모두 정적이라, 실 온프렘 빌드(플래그 없음)에서는 분기·동적 import 청크가 통째로
// 제거돼 데모 자격증명이 번들에 실리지 않는다. 실패해도 화면은 띄운다(각 쿼리 에러 표면이
// 원인을 드러낸다 — 조용한 공백 화면 금지). 로그인 화면 도입 시 이 브릿지·플래그를 제거한다.
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
