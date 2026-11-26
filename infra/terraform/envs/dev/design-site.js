// design.etforca.com viewer-request — 비밀번호 확인 뒤 경로를 정적 파일로 바꾼다.
// 비밀번호: KeyValueStore 의 basic_auth 키 = base64("아이디:비밀번호"). 키가 없거나 못 읽으면 401(닫힌 쪽).
// 경로:
//   /app, /app/<라우트>   → /app/index.html (앱 웹 빌드는 클라이언트 라우팅 — 확장자 없는 경로)
//   /screens/            → /screens/index.html
//   /screens             → 301 /screens/ (페이지 안 상대 경로가 /screens/ 기준으로 풀리게)
//   /                    → default_root_object(index.html)
//   확장자 있는 경로        → 그대로
import cf from 'cloudfront';

const kvs = cf.kvs();

function deny() {
  return {
    statusCode: 401,
    statusDescription: 'Unauthorized',
    headers: {
      'www-authenticate': { value: 'Basic realm="ETF Orca design", charset="UTF-8"' },
      'cache-control': { value: 'no-store' },
    },
  };
}

async function handler(event) {
  var request = event.request;
  var expected = null;
  try {
    expected = await kvs.get('basic_auth');
  } catch (e) {
    expected = null;
  }
  var auth = request.headers.authorization;
  if (!expected || !auth || auth.value !== 'Basic ' + expected) {
    return deny();
  }

  var uri = request.uri;
  var last = uri.split('/').pop();
  if (uri === '/app' || (uri.startsWith('/app/') && !last.includes('.'))) {
    request.uri = '/app/index.html';
  } else if (uri !== '/' && uri.endsWith('/')) {
    request.uri = uri + 'index.html';
  } else if (uri !== '/' && !last.includes('.')) {
    return {
      statusCode: 301,
      statusDescription: 'Moved Permanently',
      headers: { location: { value: uri + '/' } },
    };
  }
  return request;
}
