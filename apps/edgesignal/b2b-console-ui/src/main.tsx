import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
// ui-kit(토큰·공통 컴포넌트) → 앱 순서 고정: 앱 CSS 가 상승/하락 색 토큰을 덮는다.
import 'ui-kit/styles.css';
import './styles/app.css';

const root = document.getElementById('root');
if (!root) throw new Error('#root 엘리먼트를 찾을 수 없습니다');

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
