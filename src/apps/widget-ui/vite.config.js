import { defineConfig } from 'vite';
import { handleWidgetAnalysisRequest } from './src/mock-gateway-service.js';

// S046 local mock Gateway endpoint (PoC/dev 전용)
//
// POST /mock-gateway/widget-analysis
//   request body
//   -> mock tenantContext 생성
//   -> analysisApiClient.getLatestAnalysis({ symbol, tenantContext })
//   -> S049 gateway-adapter (mapAnalysisToWidgetResponse)
//   -> widget response
//
// 이 endpoint는 실제 Gateway가 아니다.
// - 실제 Public Embed Key 검증을 하지 않는다.
// - 실제 tenant 식별/DB 조회를 하지 않는다.
// - 실제 분석 API 호출/분석 DB 조회를 하지 않는다.
const MOCK_GATEWAY_PATH = '/mock-gateway/widget-analysis';

function mockGatewayPlugin() {
  return {
    name: 'edge-mock-gateway',
    configureServer(server) {
      server.middlewares.use(MOCK_GATEWAY_PATH, (req, res, next) => {
        if (req.method !== 'POST') {
          next();
          return;
        }

        let body = '';
        req.on('data', (chunk) => {
          body += chunk;
        });
        req.on('end', async () => {
          res.setHeader('Content-Type', 'application/json; charset=utf-8');

          let request;
          try {
            request = body ? JSON.parse(body) : {};
          } catch (error) {
            res.statusCode = 400;
            res.end(JSON.stringify({ status: 'error', message: '요청 본문 파싱 실패' }));
            return;
          }

          const widgetResponse = await handleWidgetAnalysisRequest(request);

          res.statusCode = 200;
          res.end(JSON.stringify(widgetResponse));
        });
      });
    },
  };
}

export default defineConfig({
  plugins: [mockGatewayPlugin()],
});
