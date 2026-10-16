import importlib.util
from pathlib import Path
import pytest

from psycopg.conninfo import conninfo_to_dict

spec = importlib.util.spec_from_file_location('runtime_env', Path(__file__).parents[1] / 'deploy/runtime_env.py')
runtime_env = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime_env)


def test_database_password_is_encoded_but_postgres_receives_the_original():
    password = "a@:/?#%$'z"
    values = runtime_env.build_runtime({'DEEPSEEK_API_KEY': 'unused', 'EXA_API_KEY': 'exa-test', 'PORTFOLIO_DB_PASSWORD': password, 'PORTFOLIO_ORIGIN_SECRET': 'unused'},
        {'host': 'edge-dev.cru6wwggoppd.ap-northeast-2.rds.amazonaws.com', 'dbname': 'edge', 'username': 'edge_analysis_v2_api_reader', 'password': password})
    assert values['PORTFOLIO_DB_PASSWORD'] == password
    assert values['EXA_API_KEY'] == 'exa-test'
    for key in ('PORTFOLIO_DATABASE_URL', 'PORTFOLIO_OUTLOOK_DSN', 'PORTFOLIO_NEWSLETTER_DATABASE_URL'):
        assert conninfo_to_dict(values[key])['password'] == password
    assert 'PORTFOLIO_DB_PASSWORD="a@:/?#%$$\'z"' in runtime_env.dotenv(values)


@pytest.mark.parametrize('key', [None, '', '   '])
def test_deployment_rejects_an_empty_exa_key_instead_of_using_anonymous_quota(key):
    with pytest.raises(ValueError, match='EXA_API_KEY is required'):
        runtime_env.build_runtime({'DEEPSEEK_API_KEY': 'unused', 'EXA_API_KEY': key, 'PORTFOLIO_DB_PASSWORD': 'unused', 'PORTFOLIO_ORIGIN_SECRET': 'unused'},
            {'host': 'edge-dev.cru6wwggoppd.ap-northeast-2.rds.amazonaws.com', 'dbname': 'edge', 'username': 'edge_analysis_v2_api_reader', 'password': 'unused'})
