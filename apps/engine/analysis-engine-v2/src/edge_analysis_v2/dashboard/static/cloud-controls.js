/* Cloud requests keep operational fields outside model and screen JSON. */
(async () => {
  const config = await fetch('/api/execution').then(r => r.json());
  if (config.mode !== 'cloud') return;
  const section = document.getElementById('execution');
  const details = section.querySelector('details');
  details.querySelector('summary').textContent = '클라우드 실제 분석 실행';
  details.querySelector('p').textContent = '실제 DB 기준으로 AWS에서 분석합니다. 로컬 PC를 꺼도 실행은 계속됩니다. 모델 호출 비용이 발생합니다.';
  for (const element of details.querySelectorAll('.toolbar, #cutoff')) element.hidden = true;
  const form = document.createElement('form');
  form.className = 'toolbar';
  form.innerHTML = '<label>종류 <select name="kind"><option value="outlook">전망</option><option value="movement">가격변동 설명</option></select></label>' +
    '<label>ETF 코드 <input name="etf_code" value="091160" pattern="[0-9]{6}" required></label>' +
    '<label>자료 기준시각 · 한국시간 <input name="cutoff" type="datetime-local" step="1" required></label>' +
    '<button type="submit" class="primary">클라우드에서 실행</button>';
  form.elements.cutoff.value = new Date(Date.now() + 9 * 3600000).toISOString().slice(0, 19);
  details.append(form);
  const sync = document.createElement('p');
  sync.className = 'muted';
  section.append(sync);
  let pending;
  form.onsubmit = async event => {
    event.preventDefault();
    const fields = {kind: form.elements.kind.value, etf_code: form.elements.etf_code.value,
      analysis_at: form.elements.cutoff.value + (form.elements.cutoff.value.length === 16 ? ':00' : '') + '+09:00'};
    const signature = JSON.stringify(fields);
    if (!pending || pending.signature !== signature) pending = {signature, request: {analysis_id: crypto.randomUUID().replaceAll('-', ''), ...fields}};
    const button = form.querySelector('button');
    button.disabled = true;
    try {
      const response = await fetch('/api/jobs', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': config.csrf_token}, body: JSON.stringify(pending.request)});
      const value = await response.json();
      if (!response.ok) throw new Error(value.error || '실행 요청 실패');
      location.hash = value.kind + '/' + value.analysis_id + '/execution';
      pending = null;
      document.getElementById('refresh').click();
    } catch (error) { sync.textContent = error.message; }
    finally { button.disabled = false; }
  };
  async function status() {
    try {
      const value = await fetch('/api/cloud-sync').then(r => r.json());
      sync.textContent = value.status === 'error' ? '동기화 실패 · AWS 로그인과 연결을 확인하세요. 마지막 기록은 유지됩니다.' :
        '클라우드 자동 동기화 · 마지막 수신: ' + (value.last_synced_at ? new Date(value.last_synced_at).toLocaleString('ko-KR') : '첫 기록을 확인 중');
    } catch { sync.textContent = '로컬 대시보드 연결이 끊겼습니다.'; }
  }
  status();
  setInterval(status, 5000);
})();
