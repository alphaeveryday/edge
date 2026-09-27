# app-ui

ETF Orca B2C 앱. Expo(React Native) + Expo Router.

## 이 모듈만 다른 점

- **pnpm 워크스페이스 밖의 독립 패키지**다. Metro 가 pnpm 심볼릭 링크 구조와 충돌해서 `src/pnpm-workspace.yaml` 에서 제외했고, 의존성은 이 폴더의 `package-lock.json`(npm)으로 관리한다. 여기서는 `pnpm` 이 아니라 `npm` / `npx expo install` 을 쓴다.
- **Expo Go 를 쓰지 않는다.** 처음부터 development build(`expo-dev-client`)다. `npm run ios` / `npm run android` 가 네이티브 빌드를 만든다(iOS 는 Xcode 필요).
- 화면은 `src/api` 의 클라이언트 인터페이스만 본다. `EXPO_PUBLIC_API_MODE` 가 `mock`(기본)이면 `src/api/mock`, 백엔드 연결 시 `http` 구현을 추가하고 `src/api/index.ts` 에서만 택일한다.

## 구조

```
app/            Expo Router 라우트 = 화면 (screens.md 트리)
src/api/        types · client 인터페이스 · mock 구현 · 택일 index
src/features/   도메인별 react-query 훅
src/components/ 디자인 프리미티브
src/store/      zustand (세션 등 로컬 상태)
src/theme/      토큰 (색·타이포·간격)
design/         Claude Design 원본 사본 (참고용)
```

## 명령

```
npm install
npm start          # 메트로
npm run typecheck
npm run doctor
```
