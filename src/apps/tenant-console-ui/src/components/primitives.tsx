/* EDGE Console — UI primitives (presentational, no data deps) */
import type { CSSProperties, ReactNode } from 'react';
import { Icon } from './Icon';

type Tone = 'neutral' | 'ok' | 'warn' | 'bad' | 'info' | 'prod' | 'stg';

export function Badge({
  tone = 'neutral',
  children,
  dot,
}: {
  tone?: Tone;
  children: ReactNode;
  dot?: boolean;
}) {
  return (
    <span className={'badge badge-' + tone}>
      {dot && <span className="dot" />}
      {children}
    </span>
  );
}

export function EnvBadge({ env }: { env: string }) {
  return env === 'production' || env === 'live' ? (
    <Badge tone="prod" dot>
      {env}
    </Badge>
  ) : (
    <Badge tone="stg" dot>
      {env}
    </Badge>
  );
}

export function Avatar({
  initial,
  size = 30,
  tone = 'navy',
}: {
  initial: string;
  size?: number;
  tone?: 'navy' | 'grey';
}) {
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        flex: 'none',
        background:
          tone === 'navy' ? 'linear-gradient(150deg,var(--p-500),var(--p-700))' : 'var(--n-200)',
        color: tone === 'navy' ? '#fff' : 'var(--n-600)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontWeight: 700,
        fontSize: size * 0.42,
        letterSpacing: '-.02em',
      }}
    >
      {initial}
    </div>
  );
}

export function Toggle({ on, onChange }: { on: boolean; onChange?: (v: boolean) => void }) {
  return (
    <button
      className={'tgl' + (on ? ' on' : '')}
      onClick={() => onChange && onChange(!on)}
      aria-pressed={on}
    />
  );
}

type SegOpt = string | { v: string; l: string };
export function Segmented({
  opts,
  value,
  onChange,
  block,
}: {
  opts: SegOpt[];
  value: string;
  onChange: (v: string) => void;
  block?: boolean;
}) {
  return (
    <div className={'seg' + (block ? ' seg-block' : '')}>
      {opts.map((o) => {
        const v = typeof o === 'string' ? o : o.v;
        const l = typeof o === 'string' ? o : o.l;
        return (
          <button key={v} className={value === v ? 'on' : ''} onClick={() => onChange(v)}>
            {l}
          </button>
        );
      })}
    </div>
  );
}

export function PageHeader({
  crumbs,
  title,
  desc,
  actions,
  badge,
}: {
  crumbs?: string[];
  title: string;
  desc?: string;
  actions?: ReactNode;
  badge?: ReactNode;
}) {
  return (
    <div style={{ marginBottom: 22 }}>
      {crumbs && (
        <div
          className="row gap6"
          style={{ fontSize: 12.5, color: 'var(--n-500)', fontWeight: 600, marginBottom: 11 }}
        >
          {crumbs.map((c, i) => (
            <span key={i} className="row gap6">
              {i > 0 && <Icon n="chevR" s={13} style={{ color: 'var(--n-300)' }} />}
              <span style={{ color: i === crumbs.length - 1 ? 'var(--n-700)' : 'var(--n-500)' }}>
                {c}
              </span>
            </span>
          ))}
        </div>
      )}
      <div className="row spread gap16" style={{ alignItems: 'flex-end' }}>
        <div style={{ minWidth: 0 }}>
          <div className="row gap10" style={{ alignItems: 'center' }}>
            <h1
              style={{
                fontSize: 24,
                fontWeight: 700,
                letterSpacing: '-.022em',
                color: 'var(--n-900)',
              }}
            >
              {title}
            </h1>
            {badge}
          </div>
          {desc && (
            <p
              style={{
                fontSize: 13.5,
                color: 'var(--n-500)',
                marginTop: 6,
                maxWidth: 680,
                lineHeight: 1.55,
              }}
            >
              {desc}
            </p>
          )}
        </div>
        {actions && (
          <div className="row gap8" style={{ flex: 'none' }}>
            {actions}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * 카드형 패널. 헤더(title/sub/actions) + 본문 + 선택적 footer.
 * pad=false 로 두면 본문 패딩을 없애 테이블을 끝까지 붙일 수 있다.
 */
export function Panel({
  title,
  sub,
  actions,
  pad = true,
  footer,
  children,
  style,
}: {
  title?: ReactNode;
  sub?: ReactNode;
  actions?: ReactNode;
  pad?: boolean;
  footer?: ReactNode;
  children: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <div className="card" style={style}>
      {(title || actions) && (
        <div className="card-hd">
          <div style={{ minWidth: 0 }}>
            {title && <h3>{title}</h3>}
            {sub && <div className="card-sub">{sub}</div>}
          </div>
          {actions && (
            <div className="row gap8" style={{ flex: 'none' }}>
              {actions}
            </div>
          )}
        </div>
      )}
      <div className={pad ? 'card-pad' : ''}>{children}</div>
      {footer}
    </div>
  );
}
