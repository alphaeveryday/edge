export interface ToggleProps {
  on: boolean;
  /** 시각적 비활성. 클릭은 여전히 onToggle로 전달된다 —
   * 호출부가 안내 토스트 등으로 응답할 수 있게 하기 위함 (네이티브 disabled는 클릭을 삼킨다). */
  disabled?: boolean;
  onToggle: () => void;
  'aria-label'?: string;
}

/** 온·오프 스위치 (34×19 pill). */
export function Toggle({ on, disabled, onToggle, 'aria-label': ariaLabel }: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-disabled={disabled}
      aria-label={ariaLabel}
      className={`switch${on && !disabled ? ' on' : ''}${disabled ? ' disabled' : ''}`}
      onClick={onToggle}
    />
  );
}
