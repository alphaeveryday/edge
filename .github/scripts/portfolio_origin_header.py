"""Preserve the existing CloudFront origin gate without logging configuration."""
import json
import os
import subprocess


def read_header():
    result = subprocess.run(['aws', 'cloudfront', 'get-distribution-config', '--id', 'E1NCM7J3LQOZ7E', '--output', 'json'], capture_output=True, check=True, text=True)
    config = json.loads(result.stdout)['DistributionConfig']
    values = [h['HeaderValue'] for o in config['Origins']['Items'] if o['Id'] == 'portfolio-api'
              for h in o.get('CustomHeaders', {}).get('Items', []) if h['HeaderName'].lower() == 'x-portfolio-origin']
    if len(values) != 1 or len(values[0]) < 32 or any(c in values[0] for c in '\r\n'):
        raise RuntimeError('Existing portfolio origin gate missing or invalid')
    return values[0]


if __name__ == '__main__':
    value = read_header()
    print('::add-mask::' + value)
    with open(os.environ['GITHUB_ENV'], 'a', encoding='utf8') as output:
        output.write('TF_VAR_portfolio_origin_header=' + value + '\n')
