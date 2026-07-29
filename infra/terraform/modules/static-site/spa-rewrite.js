// SPA fallback — viewer-request 에서 확장자 없는 경로를 /index.html 로 리라이트한다.
// default behavior(S3 오리진)에만 연결 — /api/* behavior 는 이 함수를 타지 않아
// API 의 403/404 JSON 이 왜곡 없이 통과한다(ALPHA-617; 이전 custom_error_response 는
// 배포 전역이라 /api/* 에러까지 index.html 200 으로 마스킹했다).
// 판별: 마지막 경로 세그먼트에 '.' 이 없으면 SPA 라우트로 본다.
//   /tenants/run_a1b2… → /index.html   /assets/app-Xyz.js → 통과   /tenants/ → /index.html
// 전제: SPA 라우트 파라미터에 '.' 을 쓰지 않는다(현행 도메인 ID = prefix_[0-9A-Za-z]{26}, ADR-0027).
// 쿼리스트링은 event.request.querystring 별도 객체라 uri 만 바꿔도 보존된다.
function handler(event) {
  var request = event.request;
  if (!request.uri.split('/').pop().includes('.')) {
    request.uri = '/index.html';
  }
  return request;
}
