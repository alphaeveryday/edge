import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { CallApp } from './call/CallApp';
// ui-kit(토큰·공통 컴포넌트) → 앱 순서 고정: 앱 CSS 가 상승/하락 색 토큰을 덮는다.
import 'ui-kit/styles.css';
import './styles/app.css';
import './styles/call.css';

// 정적 호스팅이라 하위 경로 대신 해시로 고른다 — #/call 은 모바일 통화 화면, 그 밖은 콘솔.
const isCall = () => location.hash.startsWith('#/call');

function Root() {
  const [call, setCall] = useState(isCall);
  useEffect(() => {
    const onHash = () => setCall(isCall());
    addEventListener('hashchange', onHash);
    return () => removeEventListener('hashchange', onHash);
  }, []);
  return call ? <CallApp /> : <App />;
}

const root = document.getElementById('root');
if (!root) throw new Error('#root 엘리먼트를 찾을 수 없습니다');

createRoot(root).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
