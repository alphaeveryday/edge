"""Executed as root on the isolated host; never print credentials."""
import json
from pathlib import Path
from urllib.parse import quote

root = Path('/opt/edge-portfolio')
runtime = json.loads((root / 'runtime.json').read_text())
reader = json.loads((root / 'reader.json').read_text())
assert reader['host'] == 'edge-dev.cru6wwggoppd.ap-northeast-2.rds.amazonaws.com'
assert reader['dbname'] == 'edge'
assert 'reader' in reader['username']
runtime['PORTFOLIO_OUTLOOK_DSN'] = ('postgresql://' + quote(reader['username'], safe='') + ':' + quote(reader['password'], safe='')
    + '@' + reader['host'] + ':5432/edge?sslmode=verify-full&sslrootcert=/app/rds-ca.pem')
(root / 'runtime.env').write_text('\n'.join(key + '=' + value for key, value in runtime.items()) + '\n')
(root / 'runtime.env').chmod(0o600)
(root / 'runtime.json').unlink()
(root / 'reader.json').unlink()
