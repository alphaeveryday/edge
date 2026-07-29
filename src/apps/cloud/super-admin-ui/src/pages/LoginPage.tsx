/* 로그인 화면 (ALPHA-616, 디자인 로그인.dc.html).
 * AdminLayout 밖 공개 라우트라 .edge-root(폰트·토큰 스코프)를 스스로 감싼다.
 * 상태 매핑: 401→인라인 에러(+필드 보더), 403→차단 배너(엣지 WAF/망 — body 비 JSON 전제),
 * 그 외 ApiError→서버 오류 배너(HTTP n), fetch TypeError(네트워크)→서버 오류 배너(무상태),
 * 가드가 실어 보낸 reason:'expired'→세션 만료 배너. 성공은 화면 없이 원래 경로로 즉시 복귀.
 * 시안의 OTP·인증완료 뷰는 범위 밖(서버 2FA 미지원 — ALPHA-474 계열 후속). */
import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import { useLogin } from '../domains/session/hooks';

interface LoginLocationState {
  from?: { pathname?: string; search?: string; hash?: string };
  reason?: 'expired';
}

type Banner = { kind: 'expired' } | { kind: 'system'; status?: number } | { kind: 'blocked' };

function bodyMessage(body: unknown): string | undefined {
  if (body && typeof body === 'object' && 'message' in body) {
    const m = (body as { message?: unknown }).message;
    if (typeof m === 'string' && m) return m;
  }
  return undefined;
}

/* AdminLayout.tsx EdgeMark 의 로그인 시안 변형(다크 패널 위라 배경 사각형 없음) — 복제본. */
function BrandMark() {
  return (
    <svg width={26} height={26} viewBox="0 0 32 32" fill="none" role="img" aria-label="EDGE mark" style={{ flex: 'none' }}>
      <rect x="8" y="17" width="3.4" height="7" rx="1" fill="#71717a" />
      <rect x="14.3" y="12" width="3.4" height="12" rx="1" fill="#d4d4d8" />
      <rect x="20.6" y="8" width="3.4" height="16" rx="1" fill="#4a7bd8" />
    </svg>
  );
}

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const state = (location.state ?? null) as LoginLocationState | null;
  // 쿼리스트링·해시까지 보존 — /sources?runKey=… 딥링크 복귀 시 드릴다운 컨텍스트를 잃지 않게.
  const from = state?.from?.pathname
    ? `${state.from.pathname}${state.from.search ?? ''}${state.from.hash ?? ''}`
    : '/tenants';

  const [email, setEmail] = useState('');
  const [pw, setPw] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [fieldError, setFieldError] = useState('');
  const [banner, setBanner] = useState<Banner | null>(state?.reason === 'expired' ? { kind: 'expired' } : null);
  const login = useLogin();
  const submitting = login.isPending;

  const submit = () => {
    if (submitting) return;
    if (!email.trim() || !pw) {
      setFieldError('이메일과 비밀번호를 모두 입력하세요.');
      return;
    }
    setFieldError('');
    setBanner(null);
    login.mutate(
      { email: email.trim(), password: pw },
      {
        onSuccess: () => navigate(from, { replace: true }),
        onError: (error) => {
          if (error instanceof ApiError && error.status === 401) {
            setFieldError(bodyMessage(error.body) ?? '이메일 또는 비밀번호가 올바르지 않습니다.');
          } else if (error instanceof ApiError && error.status === 403) {
            setBanner({ kind: 'blocked' });
          } else if (error instanceof ApiError) {
            setBanner({ kind: 'system', status: error.status });
          } else {
            setBanner({ kind: 'system' }); // fetch TypeError 등 네트워크 실패 — 상태코드 없음
          }
        },
      },
    );
  };

  const invalid = fieldError !== '';
  const fieldBorder = invalid ? 'var(--down)' : undefined;

  return (
    <div className="edge-root flex min-h-screen" style={{ background: 'var(--bg-surface)' }}>
      {/* 좌측 브랜드 패널 */}
      <div
        className="flex min-w-0 flex-col justify-between"
        style={{ flex: 2.33, background: 'var(--bg-nav)', padding: '40px 56px' }}
      >
        <div className="flex items-center gap-3">
          <BrandMark />
          <span style={{ color: '#fff', fontSize: 22, fontWeight: 700, letterSpacing: '0.02em' }}>EDGE</span>
          <span
            style={{
              color: 'var(--gray-500)',
              fontSize: 10,
              fontWeight: 600,
              letterSpacing: '0.14em',
              textTransform: 'uppercase',
              border: '1px solid rgba(255,255,255,.18)',
              borderRadius: 3,
              padding: '3px 7px',
            }}
          >
            Super Admin
          </span>
        </div>
        <div className="flex flex-col gap-4" style={{ maxWidth: 460 }}>
          <span style={{ color: '#fff', fontSize: 28, fontWeight: 600, lineHeight: 1.4, letterSpacing: '-0.02em' }}>
            뉴스·공시 이벤트 기반
            <br />
            ETF 영향 분석 플랫폼
          </span>
          <span style={{ color: 'var(--gray-400)', fontSize: 13, lineHeight: 1.7 }}>
            증권사·자산운용사 테넌트, 분석 파이프라인, 컴플라이언스 정책을 한 곳에서 운영합니다.
          </span>
        </div>
        <div />
      </div>

      {/* 우측 로그인 패널 */}
      <div className="flex min-w-0 items-center justify-center" style={{ flex: 1, padding: '48px 40px' }}>
        <form
          className="flex flex-col gap-[18px]"
          style={{ width: 352, maxWidth: '100%' }}
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <span className="t-h1">로그인</span>

          {banner?.kind === 'expired' && (
            <div
              className="login-rise flex gap-2"
              style={{ padding: '10px 12px', background: '#fdf6e7', border: '1px solid #e8d9ae', borderRadius: 5 }}
            >
              <svg width={15} height={15} viewBox="0 0 24 24" fill="none" stroke="#8a6d1f" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" style={{ flex: 'none', marginTop: 1 }}>
                <circle cx="12" cy="12" r="9" />
                <path d="M12 7v5l3 2" />
              </svg>
              <div className="flex flex-col gap-px">
                <span className="t-sm" style={{ color: '#6b5416', fontWeight: 500 }}>세션이 만료되었습니다</span>
                <span className="t-xs" style={{ color: '#8a6d1f' }}>로그인 후 원래 화면으로 돌아갑니다.</span>
              </div>
            </div>
          )}

          {banner?.kind === 'system' && (
            <div
              className="login-rise flex gap-2"
              style={{ padding: '10px 12px', background: '#fdf2f2', border: '1px solid #eccfcf', borderRadius: 5 }}
            >
              <svg width={15} height={15} viewBox="0 0 24 24" fill="none" stroke="var(--down)" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" style={{ flex: 'none', marginTop: 1 }}>
                <path d="m10.29 3.86-8.18 14A1.5 1.5 0 0 0 3.4 20h17.2a1.5 1.5 0 0 0 1.29-2.14l-8.18-14a1.5 1.5 0 0 0-2.58 0Z" />
                <path d="M12 9v4" />
                <path d="M12 17h.01" />
              </svg>
              <div className="flex flex-col gap-px">
                <span className="t-sm" style={{ color: '#8c2f2f', fontWeight: 500 }}>인증 서버에 연결할 수 없습니다</span>
                <span className="t-xs" style={{ color: 'var(--down)' }}>
                  잠시 후 다시 시도하세요.{banner.status !== undefined ? ` (HTTP ${banner.status})` : ''}
                </span>
              </div>
            </div>
          )}

          {banner?.kind === 'blocked' && (
            <div
              className="login-rise flex gap-2"
              style={{ padding: '10px 12px', background: 'var(--bg-sunken)', border: '1px solid var(--border-strong)', borderRadius: 5 }}
            >
              <svg width={15} height={15} viewBox="0 0 24 24" fill="none" stroke="var(--fg-2)" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" style={{ flex: 'none', marginTop: 1 }}>
                <circle cx="12" cy="12" r="9" />
                <path d="m4.9 4.9 14.2 14.2" />
              </svg>
              <div className="flex flex-col gap-px">
                <span className="t-sm" style={{ color: 'var(--fg-1)', fontWeight: 500 }}>요청이 일시적으로 차단되었습니다</span>
                <span className="t-xs" style={{ color: 'var(--fg-3)' }}>사내망 또는 VPN에서 접속하세요.</span>
              </div>
            </div>
          )}

          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <span className="t-label">이메일</span>
              <label className="field w-full box-border" style={{ height: 38, borderColor: fieldBorder }}>
                <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
                  <rect x="2" y="4" width="20" height="16" rx="2" />
                  <path d="m2.5 6.5 9.5 7 9.5-7" />
                </svg>
                <input
                  type="email"
                  autoComplete="username"
                  placeholder="ops@edge.io"
                  value={email}
                  onChange={(e) => {
                    setEmail(e.target.value);
                    setFieldError('');
                  }}
                />
              </label>
            </div>
            <div className="flex flex-col gap-1.5">
              <span className="t-label">비밀번호</span>
              <label className="field w-full box-border" style={{ height: 38, borderColor: fieldBorder }}>
                <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
                  <rect x="4" y="10" width="16" height="11" rx="2" />
                  <path d="M8 10V7a4 4 0 0 1 8 0v3" />
                </svg>
                <input
                  type={showPw ? 'text' : 'password'}
                  autoComplete="current-password"
                  placeholder="비밀번호"
                  value={pw}
                  onChange={(e) => {
                    setPw(e.target.value);
                    setFieldError('');
                  }}
                />
                <button
                  type="button"
                  onClick={() => setShowPw((v) => !v)}
                  aria-label="비밀번호 표시 전환"
                  className="flex cursor-pointer items-center border-none bg-transparent p-0"
                  style={{ color: 'var(--fg-3)', flex: 'none' }}
                >
                  {showPw ? (
                    <svg width={15} height={15} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
                      <path d="M9.88 9.88a3 3 0 1 0 4.24 4.24" />
                      <path d="M10.73 5.08A10.4 10.4 0 0 1 12 5c7 0 10 7 10 7a13.2 13.2 0 0 1-1.67 2.68" />
                      <path d="M6.61 6.61A13.5 13.5 0 0 0 2 12s3 7 10 7a9.7 9.7 0 0 0 5.39-1.61" />
                      <line x1="2" y1="2" x2="22" y2="22" />
                    </svg>
                  ) : (
                    <svg width={15} height={15} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
                      <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" />
                      <circle cx="12" cy="12" r="3" />
                    </svg>
                  )}
                </button>
              </label>
            </div>

            {invalid && (
              <div className="login-rise flex items-center gap-1.5">
                <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="var(--down)" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" style={{ flex: 'none' }}>
                  <circle cx="12" cy="12" r="9" />
                  <path d="M12 8v4" />
                  <path d="M12 16h.01" />
                </svg>
                <span className="t-sm" style={{ color: 'var(--down)' }}>{fieldError}</span>
              </div>
            )}
          </div>

          <button
            type="submit"
            className="btn w-full justify-center"
            disabled={submitting}
            style={{ height: 40, fontSize: 13, color: '#fff', background: 'var(--bg-nav)', borderColor: 'var(--bg-nav)' }}
          >
            {submitting && (
              <svg className="animate-spin" width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
                <path d="M21 12a9 9 0 1 1-6.22-8.56" />
              </svg>
            )}
            <span>{submitting ? '확인 중…' : '로그인'}</span>
          </button>
        </form>
      </div>
    </div>
  );
}
