import { useState } from 'react';
import { Button } from './design-system/components';
import { analyticsEnabled, setAnalyticsEnabled } from './analytics';
import { preview } from './preview';

export function AnalyticsPreference() {
  const [enabled, setEnabled] = useState(analyticsEnabled);
  if (preview) return null;
  return <footer className="analytics-preference"><details><summary>이용 통계 설정</summary><p>방문 분석용 쿠키와 화면 녹화를 사용해요. 입력 내용·보유금액·이메일이 녹화에 포함될 수 있어요. 인증 쿠키는 수집하지 않아요.</p><Button aria-pressed={enabled} onClick={() => { setAnalyticsEnabled(!enabled); setEnabled(analyticsEnabled()); }}>{enabled ? '이용 통계 끄기' : '이용 통계 켜기'}</Button></details></footer>;
}
