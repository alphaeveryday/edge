// www 이동 — viewer-request 에서 www.<도메인> 으로 온 요청을 https://<도메인> 으로 301 이동시킨다.
// 경로와 쿼리스트링은 그대로 옮긴다. www 가 아닌 호스트는 손대지 않는다.
// 쿼리스트링은 event.request.querystring 객체로 오고 값은 디코드되지 않은 원문이라 그대로 이어 붙인다.
// 값 없는 키(?a)와 빈 값(?a=)은 둘 다 빈 문자열로 와서 구분되지 않는다 — 둘 다 a= 로 내보낸다.
function handler(event) {
  var request = event.request;
  var host = request.headers.host.value;
  if (host.indexOf('www.') !== 0) {
    return request;
  }
  var pairs = [];
  Object.keys(request.querystring).forEach(function (key) {
    var entry = request.querystring[key];
    (entry.multiValue || [entry]).forEach(function (item) {
      pairs.push(key + '=' + item.value);
    });
  });
  var query = pairs.length ? '?' + pairs.join('&') : '';
  return {
    statusCode: 301,
    statusDescription: 'Moved Permanently',
    headers: { location: { value: 'https://' + host.slice(4) + request.uri + query } },
  };
}
