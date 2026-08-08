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
      // 켜짐 신호(on)는 disabled 와 무관하게 붙인다 — 켜짐의 시각 표현이 전부 .on 에
      // 걸려 있어(트랙 색·손잡이 위치), 빼면 켜진 스위치가 꺼진 모양으로 그려진다.
      // aria-checked 는 on 을 그대로 실으므로 눈과 보조기술이 갈리기도 했다(ALPHA-766).
      className={`switch${on ? ' on' : ''}${disabled ? ' disabled' : ''}`}
      onClick={onToggle}
    />
  );
}
