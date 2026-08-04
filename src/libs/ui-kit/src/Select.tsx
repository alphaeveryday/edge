import { useEffect, useId, useRef, useState } from 'react';
import { Icon } from './Icon';

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export interface SelectProps {
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  /** value 가 어느 옵션과도 안 맞을 때 트리거에 뜨는 안내(선택 불가). */
  placeholder?: string;
  /** 시각적 비활성. 네이티브 select 의 disabled 와 달리 열기만 막는다. */
  disabled?: boolean;
  /** 트리거 고정 너비(px 또는 CSS 값). 미지정이면 선택값 기준 auto(+min-width). */
  width?: number | string;
  /** 폼 필드처럼 칸 전체를 채운다(구 `select w-full` 대체). */
  block?: boolean;
  'aria-label'?: string;
}

/**
 * 커스텀 드롭다운(네이티브 select 대체) — 트리거는 **선택된 값** 기준 너비라
 * 긴 옵션(예: 안내 문구)이 컨트롤을 넓히지 않는다(네이티브 select 는 가장 긴 옵션에
 * 맞춰 커져 화면마다 폭이 들쭉날쭉했다). 펼침 목록은 디자인 토큰(.popover/.menu-item)으로
 * 렌더해 콘솔 전체가 한 모양이다. 키보드(↑↓·Enter·Esc)·외부 클릭 닫힘·listbox 접근성 포함.
 */
export function Select({
  value,
  onChange,
  options,
  placeholder,
  disabled,
  width,
  block,
  'aria-label': ariaLabel,
}: SelectProps) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1); // 키보드 하이라이트 인덱스
  const rootRef = useRef<HTMLDivElement>(null);
  const listId = useId();

  const selected = options.find((o) => o.value === value);
  const triggerLabel = selected ? selected.label : (placeholder ?? '');

  // 외부 클릭·Esc 로 닫는다(Modal 의 스크림 닫힘과 같은 관례, 여기선 팝오버라 문서 리스너).
  useEffect(() => {
    if (!open) return;
    const onDocDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDocDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const openList = () => {
    if (disabled) return;
    const cur = options.findIndex((o) => o.value === value);
    setActive(cur >= 0 ? cur : 0);
    setOpen(true);
  };

  const commit = (opt: SelectOption) => {
    if (opt.disabled) return;
    onChange(opt.value);
    setOpen(false);
  };

  // 다음/이전의 선택 가능한 옵션으로 하이라이트를 옮긴다(disabled 는 건너뛴다).
  const move = (dir: 1 | -1) => {
    if (options.length === 0) return;
    let i = active;
    for (let step = 0; step < options.length; step++) {
      i = (i + dir + options.length) % options.length;
      if (!options[i].disabled) {
        setActive(i);
        return;
      }
    }
  };

  const onTriggerKey = (e: React.KeyboardEvent) => {
    if (disabled) return;
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openList();
      }
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      move(1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      move(-1);
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      if (active >= 0) commit(options[active]);
    }
  };

  return (
    <div
      ref={rootRef}
      className="select-kit"
      style={{ position: 'relative', width: block ? '100%' : width, display: block ? 'block' : 'inline-block' }}
    >
      <button
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={ariaLabel}
        aria-disabled={disabled}
        disabled={disabled}
        className="select-kit-trigger"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 'var(--sp-3)',
          width: block ? '100%' : width,
          minWidth: block ? undefined : 96,
          height: 30,
          padding: '0 var(--sp-4)',
          background: disabled ? 'var(--bg-sunken)' : 'var(--bg-surface)',
          border: '1px solid var(--border-strong)',
          borderRadius: 'var(--radius)',
          color: disabled ? 'var(--fg-4)' : 'var(--fg-1)',
          font: 'inherit',
          fontSize: 'var(--sm-size)',
          cursor: disabled ? 'not-allowed' : 'pointer',
          whiteSpace: 'nowrap',
        }}
        onClick={() => (open ? setOpen(false) : openList())}
        onKeyDown={onTriggerKey}
      >
        <span
          style={{
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            color: selected ? undefined : 'var(--fg-4)',
          }}
        >
          {triggerLabel}
        </span>
        <Icon name="chevronDown" size={14} />
      </button>

      {open && (
        <div
          id={listId}
          role="listbox"
          className="popover"
          style={{
            position: 'absolute',
            top: 'calc(100% + 4px)',
            left: 0,
            zIndex: 120,
            minWidth: '100%',
            padding: 'var(--sp-2)',
          }}
        >
          {options.map((opt, i) => (
            <div
              key={opt.value}
              role="option"
              aria-selected={opt.value === value}
              aria-disabled={opt.disabled}
              className="menu-item"
              style={{
                whiteSpace: 'nowrap',
                cursor: opt.disabled ? 'not-allowed' : 'pointer',
                color: opt.disabled ? 'var(--fg-4)' : undefined,
                background:
                  opt.value === value
                    ? 'var(--accent-tint)'
                    : i === active && !opt.disabled
                      ? 'var(--bg-hover)'
                      : undefined,
                fontWeight: opt.value === value ? 600 : undefined,
              }}
              onMouseEnter={() => !opt.disabled && setActive(i)}
              onMouseDown={(e) => {
                // mousedown 으로 커밋한다 — 트리거 blur 로 목록이 먼저 닫혀 click 이 유실되는 걸 막는다.
                e.preventDefault();
                commit(opt);
              }}
            >
              {opt.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
