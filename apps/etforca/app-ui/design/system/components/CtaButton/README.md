화면의 주요 행동을 맡는 가로로 꽉 찬 버튼이다.

## 언제 쓰나

- 화면이나 시트의 맨 아래에 두는 주요 행동 하나에 `primary`를 쓴다. 한 화면에 `primary` 버튼은 하나만 둔다.
- `dark`는 본문색 바탕의 반전 버튼이다. `primary`와 나란히 놓지 않고, 브랜드 색이 과한 자리에 대신 쓴다.
- `soft`는 취소처럼 물러나는 행동, `danger`는 삭제처럼 되돌릴 수 없는 행동에 쓴다.

## 수치

- 높이: `md` 54px, `sm` 46px. 모서리 `radius-button`. 글자는 `button` 스타일.
- 색: primary = `primary` 위 `onPrimary`, dark = `text` 위 `bg`, danger = `up` 위 `onPrimary`, soft = `surface` 위 `textSub`, 비활성 = `surface` 위 `textDisabled`.
- 누르면 0.985배로 줄어든다. 색은 바뀌지 않는다.

## 쓰는 쪽이 정하는 것

`label`(행동 이름, 동사로 끝낸다), `tone`, `size`, `disabled`, 두 버튼을 나란히 둘 때의 `grow`.

정적 재현이다. `src/components/ui/CtaButton.tsx`의 수치를 옮겼다.
