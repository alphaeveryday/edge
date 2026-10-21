import type { TaxProfile } from './TaxAccounts';

export function accountIneligibleReason(account: string, profile: TaxProfile): string {
  if (account === 'ISA') {
    if (profile.residence === '비거주자') return 'ISA는 대한민국 거주자만 가입할 수 있어요.';
    if (profile.financial_income_tax === '예') return '직전 3개 과세기간 중 금융소득종합과세 대상이었다면 ISA에 가입할 수 없어요.';
    if (profile.age === '15세 미만') return 'ISA는 만 15세부터 가입할 수 있어요.';
    if (['15~18세', '19세 미만'].includes(profile.age) && profile.previous_year_earned_income === '아니요') return '만 19세 미만은 직전년도 근로소득이 있어야 ISA에 가입할 수 있어요.';
  }
  if (account === 'IRP' && profile.irp_qualification === '해당 없음') return 'IRP는 근로자·자영업자 등 가입 대상이거나 퇴직급여 수령 대상이어야 가입할 수 있어요.';
  return '';
}
