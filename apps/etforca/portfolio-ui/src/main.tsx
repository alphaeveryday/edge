import { Button, Select } from './design-system/components';
import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { inputDraftKey, legacyDraftKey } from './inputDraft';
import './design-system/fonts.css';
import './design-system/tokens.css';
import './styles.css';
import './design-system/components.css';
import { NewsletterConfirmation } from './NewsletterConfirmation';
import { Dashboard } from './Dashboard';
import { DesignCatalog } from './design-system/Catalog';
import { preview } from './preview';
import { Analysis } from './Analysis';
import { AnalyticsPreference } from './AnalyticsPreference';

const confirmationToken = new URLSearchParams(location.hash.slice(1)).get('newsletter-confirm');
// Keep the bearer token out of subsequent navigation, analytics and screenshots of the address bar.
if (confirmationToken) history.replaceState(null, '', location.pathname);
function Entry() {
  const [token, setToken] = useState(confirmationToken);
  useEffect(() => {
    function confirmLink() {
      const next = new URLSearchParams(location.hash.slice(1)).get('newsletter-confirm');
      if (!next) return;
      history.replaceState(null, '', location.pathname);
      setToken(next);
    }
    window.addEventListener('hashchange', confirmLink);
    return () => window.removeEventListener('hashchange', confirmLink);
  }, []);
  const dashboard = new URLSearchParams(location.search).get('view') === 'dashboard' || preview === 'dashboard' || preview === 'empty';
  return <>{preview && <div className="preview-bar"><b>테스트 화면 · 실제 분석·메일 발송 없음 · {import.meta.env.VITE_BUILD_VERSION}</b><label>화면<Select value={preview} onChange={e => { location.href = '?preview=' + e.target.value; }}>{[['input', '입력'], ['queued', '접수'], ['running', '분석 중'], ['result', '결과·저장 팝업'], ['partial', '일부 자료 없음'], ['failed', '분석 실패'], ['dashboard', '대시보드'], ['empty', '빈 대시보드'], ['newsletter', '구독 확인'], ['design-system', '디자인 시스템']].map(([v, label]) => <option key={v} value={v}>{label}</option>)}</Select></label><Button onClick={() => { try { sessionStorage.removeItem(inputDraftKey); sessionStorage.removeItem(legacyDraftKey); } catch { /* Preview reset can proceed without form storage. */ } for (const key of ['edge-preview-app-dismissed-until', 'edge-preview-dashboard-visited', 'edge-preview-workbook', 'edge-preview-empty-workbook', 'edge-preview-job', 'edge-preview-input', 'edge-chat-preview', 'edge-chat-preview-recovery', 'edge-portfolio-feedback:preview']) localStorage.removeItem(key); location.reload(); }}>테스트 초기화</Button></div>}{preview === 'design-system' && !token ? <DesignCatalog /> : token || preview === 'newsletter' ? <NewsletterConfirmation key={token} token={token || 'preview'} /> : dashboard ? <Dashboard /> : <Analysis />}<AnalyticsPreference /></>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><Entry /></React.StrictMode>);
