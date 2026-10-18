import { Button, TextInput } from './design-system/components';

export function SeedMoney({ onSubmit, busy }: { onSubmit: (amount: number) => void; busy: boolean }) {
  return <form className="seed-money" onSubmit={e => { e.preventDefault(); onSubmit(Number(new FormData(e.currentTarget).get('seed'))); }}>
    <h3>총 얼마를 투자하고 있나요?</h3><p>입력한 비중을 금액으로 바꿔 저장해요.</p>
    <label>총 투자금액 (원)<TextInput autoFocus name="seed" inputMode="numeric" type="number" min="1" max="10000000000000" step="1" required disabled={busy} /></label>
    <Button variant="primary" loading={busy}>금액 적용하고 저장</Button>
  </form>;
}
