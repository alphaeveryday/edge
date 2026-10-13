"""Verify the actual SSM port tunnel from the trusted dev deployment runner."""
import json
import os
import subprocess
import time
from urllib.request import urlopen

import boto3

from admin_smoke import main as verify


def main():
    if os.getenv('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('Run this check in the trusted GitHub deployment only')
    ssm = boto3.client('ssm', region_name='ap-northeast-2')
    config = json.loads(ssm.get_parameter(Name='/edge/portfolio/admin-connection')['Parameter']['Value'])
    parameters = {'Target': config['instance_id'], 'DocumentName': config['document']}
    session = ssm.start_session(**parameters)
    process = None
    try:
        process = subprocess.Popen(['session-manager-plugin', json.dumps(session), 'ap-northeast-2',
            'StartSession', '', json.dumps(parameters), 'https://ssm.ap-northeast-2.amazonaws.com'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        for _ in range(60):
            if process.poll() is not None:
                raise RuntimeError('SSM port tunnel exited before verification')
            try:
                with urlopen('http://127.0.0.1:18080/health', timeout=2) as response:
                    assert json.load(response) == {'service': 'orca-admin-ssm'}
                break
            except OSError:
                time.sleep(1)
        else:
            raise TimeoutError('SSM port tunnel did not become ready')
        verify()
    finally:
        try:
            ssm.terminate_session(SessionId=session['SessionId'])
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == '__main__':
    main()
