import logoUrl from '../../assets/logo.svg';

/* 공식 로고 (남색 워드마크, 원본에 흰 배경 포함) — 다크 패널 위에서도 원색을
 * 지키도록 흰 라운드 칩으로 감싼다. super-admin-ui 의 동명 컴포넌트와 의도적 복제
 * (cloud/onprem 모듈 경계 — 공유 배선보다 복제가 싸다, ALPHA-930·931). */
export function EdgeLogo({ height = 20 }: { height?: number }) {
  return (
    <span
      className="flex items-center"
      style={{ flex: 'none', background: '#fff', borderRadius: 6, padding: '3px 8px' }}
    >
      <img src={logoUrl} alt="EDGE" style={{ height, display: 'block' }} />
    </span>
  );
}
