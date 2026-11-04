"""격리된 부하 실험 DB에 JWT의 주체가 될 활동 회원 준비."""
import math
import re


def iterations(rate, duration):
    units = {'h': 3600, 'm': 60, 's': 1, 'ms': .001, 'us': .000001, 'µs': .000001, 'ns': .000000001}
    parts = re.findall(r'(\d+(?:\.\d+)?)(ns|us|µs|ms|s|m|h)', duration)
    if not parts or ''.join(n + unit for n, unit in parts) != duration:
        raise ValueError('Invalid experiment duration: ' + duration)
    return math.ceil(int(rate) * sum(float(n) * units[unit] for n, unit in parts))


def seed_members(sql, count, first=1):
    first, count = int(first), int(count)
    if first < 1 or count < 1:
        raise ValueError('Member range must be positive')
    # 기존 회원을 되살리거나 덮어쓰지 않는다. 러너가 만든 로컬 compose DB 전용이다.
    sql(f"insert into member(id, provider, nick, handle) "
        f"select id, 'email', '실험 회원', '@experiment-' || id "
        f"from generate_series({first}, {first + count - 1}) as id "
        "on conflict (id) do nothing;")
