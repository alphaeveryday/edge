import logoUrl from 'ui-kit/assets/edge-logo-black.svg';

/* 공식 로고 — 투명 배경 흰 워드마크(edge-logo-black.svg, 명명은 배경 기준).
 * 캐노니컬은 ui-kit 자산 하나를 pages·양 콘솔이 공용한다. 사용처가 다크
 * 패널(--bg-nav)이라 흰 로고 단일 사용. super-admin-ui 의 동명 컴포넌트와 의도적 복제
 * (cloud/onprem 모듈 경계 — 공유 배선보다 복제가 싸다, ALPHA-930·931). */
export function EdgeLogo({ height = 20 }: { height?: number }) {
  return <img src={logoUrl} alt="EDGE" style={{ height, display: 'block', flex: 'none' }} />;
}
