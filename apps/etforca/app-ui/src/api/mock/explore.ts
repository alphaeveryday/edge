// 탐색 순위 행의 헤드라인과 키워드 칩
export const RANK_META: Record<string, { title: string; chips: string[]; ready: boolean }> = {
  AXAI: { title: '메모리 값 3개월째 상승, SK하이닉스 이익률 49%', chips: ['환가 상승 지속', '고부가 비중 확대'], ready: true },
  DEFN: { title: '유럽 무기 주문 3분기 연속 증가, 한화에어로 5년치 일감', chips: ['수주 잔고 확대', '수익성 개선'], ready: true },
  GRID: { title: '美 전기료 인상 승인, 전력회사 배당 2.4%로 상향', chips: ['매출 확정', '배당 매력 확대'], ready: false },
  KBND: { title: '물가 3개월째 2%대, 한국은행 금리 인하 기대 확대', chips: ['인하 여건 성숙', '채권 가격 상승'], ready: false },
  SOLR: { title: '태양광 원료값 2년 만에 바닥, 중국 감산 발표는 아직', chips: ['원료 값 바닥', '감산 대기'], ready: false },
  MEDX: { title: '바이오 임상 3상 발표 연기, 셀트리온 3분기 실적 하향', chips: ['임상 지연', '실적 하향'], ready: false },
};
