import crypto from 'k6/crypto';
import encoding from 'k6/encoding';

// 앱 AccessTokens 와 같은 HS256 액세스 JWT 의 k6 쪽 서명
// 기본 비밀키는 compose 의 APP_JWT_SECRET 기본값
const secret = __ENV.APP_JWT_SECRET || 'local-experiment-jwt-secret-32bytes-min';
const header = encoding.b64encode(JSON.stringify({ alg: 'HS256' }), 'rawurl');
const iat = Math.floor(Date.now() / 1000);

export function bearer(member) {
  const payload = encoding.b64encode(JSON.stringify({ sub: String(member), iat, exp: iat + 86400 }), 'rawurl');
  const signature = crypto.hmac('sha256', secret, `${header}.${payload}`, 'base64rawurl');
  return `Bearer ${header}.${payload}.${signature}`;
}
