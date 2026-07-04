import { Routes, Route } from 'react-router-dom';

// super-admin-ui 골자 — 운영자 인증·cross-tenant 관리 화면은 ALPHA-288 에서 구현한다.
// 지금은 빌드·배포(S3+CloudFront) 파이프라인을 세우기 위한 최소 셸이다(tenant-console-ui 와 동일 스택).
export function App() {
  return (
    <Routes>
      <Route path="/" element={<Placeholder />} />
    </Routes>
  );
}

function Placeholder() {
  return (
    <main className="admin-placeholder">
      <h1>EDGE Admin</h1>
      <p>운영자 콘솔 준비 중입니다.</p>
    </main>
  );
}
