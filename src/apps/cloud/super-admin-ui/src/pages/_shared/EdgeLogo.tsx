import logoUrl from '../../assets/logo.svg';

/* 공식 로고 (남색 워드마크, 원본에 흰 배경 포함) — 다크 패널 위에서도 원색을
 * 지키도록 흰 라운드 칩으로 감싼다. AdminLayout 사이드바·LoginPage 가 공유. */
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
