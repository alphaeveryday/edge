import type { Asset } from './workbook';

// Exposure is independent of listing market/currency. Unrecognized names stay unclassified.
const regions: [string, RegExp][] = [
  ['글로벌', /글로벌|전세계|WORLD|GLOBAL|ALL.WORLD|TOTAL WORLD|ACWI/i],
  ['미국', /미국|\bUS\b|U\.S\.|\bUSA\b|S&P\s?500|나스닥|NASDAQ|DOW JONES|SCHWAB U.S./i],
  ['중국', /중국|차이나|CHINA|항셍|HANG SENG/i],
  ['일본', /일본|JAPAN|닛케이|NIKKEI|TOPIX/i],
  ['인도', /인도(?!네시아)|\bINDIA\b|NIFTY/i],
  ['유럽', /유럽|EUROPE|EURO STOXX|유로스탁스/i],
  ['아시아', /아시아|ASIA|베트남|VIETNAM|대만|TAIWAN/i],
  ['신흥국', /신흥국|EMERGING/i],
  ['국내', /한국|국내|KOREA|코스피|코스닥|KOSPI|KOSDAQ|^(KODEX|TIGER|ACE|RISE|PLUS|KIWOOM)\s+(200|150)(\D|$)/i],
];
const themes: [string, RegExp][] = [
  ['커버드콜', /커버드콜|COVERED CALL|EQUITY PREMIUM|BUYWRITE/i],
  ['반도체', /반도체|SEMICONDUCTOR/i],
  ['AI·기술', /인공지능|\bAI\b|테크|\bTECH|로봇|ROBOT|나스닥|NASDAQ|소프트웨어|SOFTWARE/i],
  ['배당', /배당|DIVIDEND/i],
  ['채권', /채권|국채|회사채|BOND|TREASURY|국고채/i],
  ['금·원자재', /금현물|금선물|골드|\bGOLD\b|원자재|\bCOMMODIT(?:Y|IES)\b|원유|\bCRUDE\b|\bSILVER\b|은선물/i],
  ['부동산', /부동산|리츠|REIT|REAL ESTATE/i],
  ['헬스케어', /바이오|헬스|HEALTH|BIOTECH/i],
  ['친환경·에너지', /에너지|ENERGY|2차전지|이차전지|BATTERY|친환경|CLEAN/i],
  ['금융', /금융|은행|FINANCIAL|BANK/i],
  ['시장 대표', /S&P\s?500|\b200\b|\b150\b|TOTAL STOCK|TOTAL MARKET|WORLD|ACWI/i],
];
const known: Record<string, [string, string]> = {
  'US:VOO': ['미국', '시장 대표'], 'US:SPY': ['미국', '시장 대표'], 'US:IVV': ['미국', '시장 대표'],
  'US:VTI': ['미국', '시장 대표'], 'US:QQQ': ['미국', 'AI·기술'], 'US:QQQM': ['미국', 'AI·기술'],
  'US:SCHD': ['미국', '배당'], 'US:JEPI': ['미국', '커버드콜'], 'US:JEPQ': ['미국', '커버드콜'],
  'US:VT': ['글로벌', '시장 대표'], 'US:VXUS': ['미국 외', '시장 대표'], 'US:VEA': ['미국 외', '시장 대표'],
  'US:TLT': ['미국', '채권'], 'US:BND': ['미국', '채권'], 'US:GLD': ['글로벌', '금·원자재'],
  'KR:114260': ['국내', '채권'], 'KR:132030': ['글로벌', '금·원자재'],
};
export function classifyAsset(asset: Pick<Asset, 'instrument_id' | 'name' | 'category'>) {
  if (asset.instrument_id === 'CASH:KRW' || asset.category === '현금') return { region: '현금', theme: '현금' };
  const mapped = known[asset.instrument_id ?? ''];
  const theme = mapped?.[1] ?? themes.find(([, pattern]) => pattern.test(asset.name))?.[0] ?? '미분류';
  const excludesRegion = /\bex[\s-]+|제외/i.test(asset.name);
  const exposureName = asset.name.replace(/^Global X\s+/i, '');
  const region = mapped?.[0] ?? (excludesRegion ? '미분류' : regions.find(([, pattern]) => pattern.test(exposureName))?.[0] ?? (theme === '금·원자재' ? '글로벌' : '미분류'));
  return { region, theme };
}
