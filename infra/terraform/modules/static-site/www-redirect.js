// www 이동 — viewer-request 에서 www.<도메인> 으로 온 요청을 https://<도메인> 으로 영구 이동시킨다.
// GET·HEAD 는 301, 그 밖의 메서드는 308 이다 — 301 은 브라우저가 POST 를 GET 으로 바꾸고 본문을 버린다.
// 경로와 쿼리스트링은 그대로 옮긴다. www 가 아닌 호스트는 손대지 않는다.
// 쿼리스트링은 파싱된 event.request.querystring 을 다시 조립하지 않고 원문(rawQueryString, 런타임 2.0)을
// 쓴다 — 조립하면 떨어져 있는 같은 키의 순서가 바뀌고 값 없는 키(?a)가 ?a= 가 된다.
// rawQueryString() 은 '?' 를 뺀 문자열을 주고, '?' 자체가 없으면 undefined 다.
function handler(event) {
  var request = event.request;
  var host = request.headers.host.value;
  if (host.indexOf('www.') !== 0) {
    return request;
  }
  var raw = request.rawQueryString();
  var query = raw === undefined ? '' : '?' + raw;
  var keepsMethod = request.method !== 'GET' && request.method !== 'HEAD';
  return {
    statusCode: keepsMethod ? 308 : 301,
    statusDescription: keepsMethod ? 'Permanent Redirect' : 'Moved Permanently',
    headers: { location: { value: 'https://' + host.slice(4) + request.uri + query } },
  };
}
