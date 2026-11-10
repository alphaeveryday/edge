import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { PhoneApp } from './call/PhoneApp';
// ui-kit(토큰·공통 컴포넌트) → 앱 순서 고정: 앱 CSS 가 상승/하락 색 토큰을 덮는다.
import 'ui-kit/styles.css';
import './styles/app.css';
import './styles/call.css';
import './styles/phone.css';

// 정적 호스팅이라 해시로 고른다 — #/phone/{방} 은 콘솔의 [휴대폰 연결] QR 이 여는 휴대폰 전화 화면(ALPHA-1490).
const phoneRoom = /^#\/phone\/([A-Za-z0-9]{8,32})$/.exec(location.hash)?.[1];

const root = document.getElementById('root');
if (!root) throw new Error('#root 엘리먼트를 찾을 수 없습니다');

createRoot(root).render(
  <StrictMode>
    {phoneRoom ? <PhoneApp room={phoneRoom} /> : <App />}
  </StrictMode>,
);
