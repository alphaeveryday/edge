# tenant-console-ui — Vite SPA 빌드(node) → nginx 정적 서빙 + /api 프록시(온프렘 co-host).
# 빌드 컨텍스트 = src/ (pnpm 워크스페이스 루트). ui-kit 은 빌드 없는 소스 패키지라
# vite 가 그 TS 소스를 직접 번들한다 — 워크스페이스 전체가 컨텍스트에 있어야 workspace:* 가 풀린다.
#   docker build -f apps/onprem/tenant-console-ui/Dockerfile -t tenant-console-ui .
FROM node:20-alpine AS build
WORKDIR /workspace
RUN corepack enable && corepack prepare pnpm@9.15.9 --activate
COPY . .
# 데모 박스도 실 온프렘과 동일 빌드 — 자동 세션 플래그 없음. 콘솔이 CloudFront 로 공개되므로
# (ALPHA-627) 자격증명 baked 빌드를 두지 않고, 로그인 화면(ALPHA-626)이 유일한 진입이다.
RUN pnpm install --frozen-lockfile && pnpm --filter tenant-console-ui build

# nginx 가 dist 를 서빙하고 /api 를 tenant-console-api 로 프록시한다(같은 오리진 co-host).
FROM nginx:alpine AS runtime
COPY --from=build /workspace/apps/onprem/tenant-console-ui/dist /usr/share/nginx/html
COPY apps/onprem/tenant-console-ui/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
