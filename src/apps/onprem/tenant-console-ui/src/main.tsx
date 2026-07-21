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

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
