import { Component, type ErrorInfo, type ReactNode } from 'react';

/**
 * ErrorBoundary — 렌더 예외를 흰 화면 대신 안내 카드로 표면화한다 (Rule 12 — 조용한 실패 금지).
 * 레이아웃의 페이지 영역(<Outlet/>)을 감싸 쓴다. 라우트 전환 시 리셋은 감싸는 쪽의
 * 리마운트(key)에 맡긴다 — 양 콘솔 레이아웃의 <main key={pathname}>이 그 역할이다.
 */
export class ErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  state = { hasError: false };

  static getDerivedStateFromError(): { hasError: boolean } {
    return { hasError: true };
  }

  componentDidCatch(error: unknown, info: ErrorInfo): void {
    console.error('화면 렌더 실패:', error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div className="card card-pad" style={{ fontSize: 12, color: 'var(--down)' }}>
          화면을 표시하지 못했습니다. 새로고침하거나 다른 메뉴로 이동해 주세요.
        </div>
      );
    }
    return this.props.children;
  }
}
