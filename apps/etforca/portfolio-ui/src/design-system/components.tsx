import type { ComponentProps, ReactNode } from 'react';
export { Icon } from 'ui-kit';

export function Button({ variant = 'plain', loading = false, className = '', disabled, children, ...props }: ComponentProps<'button'> & { variant?: 'primary' | 'secondary' | 'plain' | 'danger'; loading?: boolean }) {
  return <button {...props} className={`ds-button ${className}`} data-variant={variant} disabled={disabled || loading} aria-busy={loading || undefined}>{loading && <span className="ds-spinner" aria-hidden="true" />}{children}</button>;
}

export function TextInput({ className = '', ...props }: ComponentProps<'input'>) {
  return <input {...props} className={`ds-input ${className}`} />;
}
export function Select({ className = '', ...props }: ComponentProps<'select'>) {
  return <select {...props} className={`ds-select ${className}`} />;
}
export function TextArea({ className = '', ...props }: ComponentProps<'textarea'>) {
  return <textarea {...props} className={`ds-textarea ${className}`} />;
}
export function SectionHeading({ title, description, action, className = '' }: { title: string; description?: string; action?: ReactNode; className?: string }) {
  return <div className={`ds-section-heading ${className}`}><div><h2>{title}</h2>{description && <p>{description}</p>}</div>{action}</div>;
}
export function Notice({ tone = 'info', children }: { tone?: 'info' | 'error' | 'success'; children: ReactNode }) {
  return <p className="ds-notice" data-tone={tone} role={tone === 'error' ? 'alert' : 'status'}>{children}</p>;
}

export const chartPalette = Array.from({ length: 6 }, (_, i) => `var(--chart-${i + 1})`);

export function Loading({ children }: { children: ReactNode }) {
  return <div className="ds-loading" role="status"><span className="ds-spinner" aria-hidden="true" /><span>{children}</span></div>;
}
