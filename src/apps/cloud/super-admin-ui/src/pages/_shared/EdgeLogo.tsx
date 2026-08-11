import logoUrl from 'ui-kit/assets/edge-logo-black.svg';

/* 공식 로고 — 투명 배경 흰 워드마크(edge-logo-black.svg, 명명은 배경 기준).
 * 캐노니컬은 ui-kit 자산 하나를 pages·양 콘솔이 공용한다. 사용처가 전부
 * 다크 패널(--bg-nav)이라 흰 로고 단일 사용. AdminLayout 사이드바·LoginPage 가 공유. */
export function EdgeLogo({ height = 20 }: { height?: number }) {
  return <img src={logoUrl} alt="EDGE" style={{ height, display: 'block', flex: 'none' }} />;
}
