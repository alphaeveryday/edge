"""격리된 부하 실험 DB에 투표 대상 ETF 준비."""
from binascii import crc_hqx
import re


def cluster_etfs(base, slot_ranges, per_shard=10):
    # cluster.js의 CRC16·6자리 코드·샤드별 순차 선택과 동일한 대상.
    ranges = [tuple(map(int, item.split('-'))) for item in slot_ranges.split(',')]
    shards = [[] for _ in ranges]
    per_shard = int(per_shard)
    if per_shard < 1:
        raise ValueError('FORECASTS_PER_SHARD must be positive')
    for number in range(int(base), int(base) + 1000000):
        code = f'{number % 1000000:06d}'
        slot = crc_hqx(code.encode(), 0) % 16384
        for index, (lo, hi) in enumerate(ranges):
            if lo <= slot <= hi:
                if len(shards[index]) < per_shard:
                    shards[index].append(code)
                break
        if all(len(shard) == per_shard for shard in shards):
            return [code for shard in shards for code in shard]
    raise ValueError('Not enough ETF codes for the configured slot ranges')


def seed_etfs(sql, codes):
    codes = list(codes)
    if not codes or any(not re.fullmatch(r'[0-9A-Z]{6}', code) for code in codes):
        raise ValueError('ETF codes must contain six uppercase letters or digits')
    values = ','.join(f"('{code}', 'experiment-{code}', 'XKRX', '실험 ETF', 'market')" for code in codes)
    sql('insert into etf(code, instrument_id, market_code, name, theme_key) values '
        + values + ' on conflict (code) do nothing;')
