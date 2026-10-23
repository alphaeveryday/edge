export type TaxProfile = { residence: string; age: string; income: string; financial_income_tax: string; previous_year_earned_income?: string; irp_qualification?: string; recommended_accounts: string[]; accounts: { account: string; subtype: string; opened: string; remaining_man: string; paid_man: string }[] };

export function accountIneligibleReason(account: string, profile: TaxProfile): string {
  if (account === 'ISA') {
    if (profile.residence === '비거주자') return '국내 거주자만 가입할 수 있어요';
    if (profile.financial_income_tax === '예') return '최근 3년 안에 금융소득종합과세 대상이면 가입할 수 없어요';
    if (profile.age === '15세 미만') return '만 15세부터 가입할 수 있어요';
    if (['15~18세', '19세 미만'].includes(profile.age) && profile.previous_year_earned_income === '아니요') return '만 19세 미만은 지난해 근로소득이 있어야 해요';
  }
  if (account === 'IRP' && profile.irp_qualification === '해당 없음') return '소득이나 퇴직금이 있어야 해요';
  return '';
}
