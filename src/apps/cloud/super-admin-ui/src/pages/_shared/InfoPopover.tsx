/* 설명 (i) — 클릭으로 여는 팝오버 (ALPHA-738).
 *
 * 이전에는 title 속성 하나에 설명이 들어 있어 마우스 hover 로만 읽을 수 있었다 — 키보드·터치
 * 사용자에게는 설명이 아예 없는 것과 같았다. 이 컴포넌트는 그 자리를 진짜 버튼으로 바꾼다.
 *
 * 패널은 portal + position:fixed 로 띄운다. 표 안의 (i) 가 많은데, 표의 overflow 컨테이너
 * 안에서 절대배치하면 설명이 잘린다.
 */
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { MouseEvent } from 'react';
import { createPortal } from 'react-dom';
import '../../styles/info-popover.css';

/** 다른 (i) 가 열리면 나머지는 닫는다 — 전역 스토어 없이 이벤트 하나로 */
const OPEN_EVENT = 'edge:info-popover-open';
const MARGIN = 8;

export interface InfoPopoverProps {
  /** 설명 본문. 줄바꿈(\n)과 목록 구조는 그대로 보존된다 */
  text: string;
  /** 무엇에 대한 설명인지 — aria-label 은 "설명"만으로는 쓸모가 없다(예: "완전성 설명") */
  label: string;
  /** 패널 상단에 붙는 제목. 생략하면 label 을 쓴다 */
  title?: string;
}

export function InfoPopover({ text, label, title }: InfoPopoverProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  /**
   * ⚠️ 전파를 끊는다. 이 버튼은 표 행·카드처럼 **자기 클릭을 갖는 조상 안**에 놓인다 —
   * 안 끊으면 설명을 열자마자 그 행이 이동해 팝오버가 사라진다(사건 목록에서 실제로 겪었다).
   * 조상마다 막지 않고 여기서 한 번 막는다 — 새 소비자가 같은 함정을 다시 밟지 않게.
   */
  const toggle = (e: MouseEvent) => {
    e.stopPropagation();
    if (!open) document.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: id }));
    setOpen((v) => !v);
  };

  /* 다른 팝오버가 열렸다는 통지 */
  useEffect(() => {
    const onOther = (e: Event) => {
      if ((e as CustomEvent<string>).detail !== id) setOpen(false);
    };
    document.addEventListener(OPEN_EVENT, onOther);
    return () => document.removeEventListener(OPEN_EVENT, onOther);
  }, [id]);

  /* 바깥 클릭·Escape 로 닫기. Escape 는 포커스를 버튼으로 돌려준다 */
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        btnRef.current?.focus();
      }
    };
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node | null;
      if (t && (btnRef.current?.contains(t) || panelRef.current?.contains(t))) return;
      setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onDown);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onDown);
    };
  }, [open]);

  /* 화면 밖으로 잘리지 않게 배치. 스크롤·리사이즈에는 닫지 않고 따라간다 —
   * 패널 안에서 긴 설명을 스크롤해 읽는 중에 닫히면 안 된다. */
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const b = btnRef.current?.getBoundingClientRect();
      const p = panelRef.current?.getBoundingClientRect();
      if (!b || !p) return;
      const left = Math.max(MARGIN, Math.min(b.left, window.innerWidth - p.width - MARGIN));
      const below = b.bottom + 6;
      const above = b.top - p.height - 6;
      const top =
        below + p.height <= window.innerHeight - MARGIN
          ? below
          : above >= MARGIN
            ? above
            : Math.max(MARGIN, window.innerHeight - p.height - MARGIN);
      setPos((prev) => (prev && prev.left === left && prev.top === top ? prev : { left, top }));
    };
    place();
    window.addEventListener('scroll', place, true);
    window.addEventListener('resize', place);
    return () => {
      window.removeEventListener('scroll', place, true);
      window.removeEventListener('resize', place);
    };
  }, [open]);

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className="info-btn"
        aria-label={`${label} 설명`}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={toggle}
      >
        <span aria-hidden="true">i</span>
      </button>
      {open &&
        createPortal(
          <div
            ref={panelRef}
            id={id}
            role="dialog"
            aria-modal="false"
            aria-label={`${label} 설명`}
            className="info-panel"
            /* 첫 페인트는 측정용이라 화면 밖에서 시작한다(깜빡임 없이 제자리로 온다) */
            style={{ left: pos?.left ?? -9999, top: pos?.top ?? -9999 }}
          >
            <span className="info-panel-title">{title ?? label}</span>
            {text}
          </div>,
          document.body,
        )}
    </>
  );
}
