# mock-broker — 증권사 자체 제작 API 데모 서버 + 가상 MTS 정적 서빙.
# 빌드 컨텍스트는 demo/ (mts-ai-tab 정적 자산을 함께 담는다).
FROM node:20-alpine

WORKDIR /app
COPY mock-broker/server.js ./
COPY mts-ai-tab ./mts-ai-tab

ENV STATIC_DIR=/app/mts-ai-tab
EXPOSE 8080
CMD ["node", "server.js"]
