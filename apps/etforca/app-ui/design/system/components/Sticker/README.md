ETF의 오늘 전망을 다섯 단계로 보여 주는 표식이다.

## 언제 쓰나

- 전망을 말하는 모든 자리에 이 스티커를 쓴다. 글자색만 바꾼 텍스트로 전망을 대신하지 않는다.
- 다섯 단계는 강력 하락, 하락, 중립, 상승, 강력 상승이고, 나열할 때는 이 순서를 지킨다.
- 가격 등락(+2.41%)에는 쓰지 않는다. 등락은 `mono` 글자에 `up`, `down` 색으로 적는다.

## 수치

- 높이 28px, 최소 너비 44px, 좌우 여백 9px, 모서리 9px, 테두리 `glossLine`.
- 기호: 상승 ▲, 중립 ■, 하락 ▼. 강력 단계는 기호를 두 줄로 겹친다. 기호 크기는 13px, 겹칠 때 9px.
- 색: 바탕 `signalAlpha-up|neutral|down`, 기호 `up|neutral|down`, 글자 `upDeep|neutralDeep|downDeep`. 글자는 `sticker` 스타일.
- 위에 `gloss-from`에서 `gloss-to`로 가는 광택을 얹는다.
- 방향은 색과 기호와 글자 세 가지로 함께 전한다. 글자를 숨기는 변형(`showLabel` 끔)에서도 기호는 남는다.

## 쓰는 쪽이 정하는 것

`signal`(다섯 단계 중 하나), 크기 `size`, 글자 표시 여부 `showLabel`.

정적 재현이다. `src/components/ui/Sticker.tsx`와 `src/theme/tokens.ts`의 수치를 옮겼다.
