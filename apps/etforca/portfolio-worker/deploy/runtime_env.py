"""Executed as root on the isolated host; never print credentials."""
import json
from pathlib import Path
from urllib.parse import quote


def build_runtime(runtime, reader):
    if reader['host'] != 'edge-dev.cru6wwggoppd.ap-northeast-2.rds.amazonaws.com' or reader['dbname'] != 'edge' or reader['username'] != 'edge_analysis_v2_api_reader':
        raise ValueError('Unexpected outlook database identity')
    values = {key: runtime[key] for key in ('DEEPSEEK_API_KEY', 'EXA_API_KEY', 'PORTFOLIO_DB_PASSWORD', 'PORTFOLIO_ORIGIN_SECRET')}
    if not isinstance(values['EXA_API_KEY'], str) or not values['EXA_API_KEY'].strip():
        raise ValueError('EXA_API_KEY is required')
    values['PORTFOLIO_DATABASE_URL'] = 'postgresql://portfolio:' + quote(values['PORTFOLIO_DB_PASSWORD'], safe='') + '@db/portfolio'
    values['PORTFOLIO_NEWSLETTER_DATABASE_URL'] = 'postgresql://portfolio:' + quote(values['PORTFOLIO_DB_PASSWORD'], safe='') + '@127.0.0.1:15432/portfolio'
    values['PORTFOLIO_OUTLOOK_DSN'] = ('postgresql://' + quote(reader['username'], safe='') + ':' + quote(reader['password'], safe='')
        + '@' + reader['host'] + ':5432/edge?sslmode=verify-full&sslrootcert=/app/rds-ca.pem')
    return values


def dotenv(values):
    if any(not isinstance(value, str) or '\n' in value or '\r' in value for value in values.values()):
        raise ValueError('Runtime values must be single-line strings')
    # Double-quote escapes are JSON-compatible; $$ suppresses Compose interpolation.
    return '\n'.join(key + '=' + json.dumps(value.replace('$', '$$'), ensure_ascii=False) for key, value in values.items()) + '\n'


def main():
    root = Path('/opt/edge-portfolio')
    values = build_runtime(json.loads((root / 'runtime.json').read_text()), json.loads((root / 'reader.json').read_text()))
    (root / 'runtime.env').write_text(dotenv(values))
    (root / 'runtime.env').chmod(0o600)
    (root / 'runtime.json').unlink()
    (root / 'reader.json').unlink()


if __name__ == '__main__':
    main()
