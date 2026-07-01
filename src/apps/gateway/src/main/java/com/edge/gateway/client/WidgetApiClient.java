package com.edge.gateway.client;

import com.edge.gateway.widget.InternalAnalysisRequest;

/** widget-api 내부 엔드포인트 호출 계약. 응답은 위젯 표준 응답 JSON(raw) — gateway는 형태를 알지 않고 pass-through. */
public interface WidgetApiClient {

    String analyze(InternalAnalysisRequest request);
}
