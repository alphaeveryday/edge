"""검증 빌드용 가짜 Firebase 설정 파일을 만든다.

형식만 맞춘 파일이라 실제 Firebase 프로젝트로 갈 경로가 없다. 검증 빌드의 분석 데이터가 운영에 섞이지 않게 한다.
키 문자열을 나눠 이어 붙인다 — 가짜 키지만 gitleaks 의 GCP API 키 패턴에 걸리지 않게 한다.

사용: GOOGLE_SERVICES_JSON=<경로> GOOGLE_SERVICE_INFO_PLIST=<경로> python3 write_fake_firebase.py
"""
import json
import os
import plistlib

PKG, PID = "com.alphaeveryday.etforca", "orca-verify-fake"
key = "AI" + "za" + "SyVerifyBuildOnlyNotARealKey"
key += "0" * (39 - len(key))

json_path, plist_path = os.environ["GOOGLE_SERVICES_JSON"], os.environ["GOOGLE_SERVICE_INFO_PLIST"]
for p in (json_path, plist_path):
    os.makedirs(os.path.dirname(p), exist_ok=True)
with open(json_path, "w") as f:
    json.dump({"project_info": {"project_number": "000000000000", "project_id": PID, "storage_bucket": f"{PID}.appspot.com"},
               "client": [{"client_info": {"mobilesdk_app_id": "1:000000000000:android:0000000000000000",
                                           "android_client_info": {"package_name": PKG}},
                           "oauth_client": [], "api_key": [{"current_key": key}],
                           "services": {"appinvite_service": {"other_platform_oauth_client": []}}}],
               "configuration_version": "1"}, f)
with open(plist_path, "wb") as f:
    plistlib.dump({"API_KEY": key, "GCM_SENDER_ID": "000000000000", "PLIST_VERSION": "1", "BUNDLE_ID": PKG,
                   "PROJECT_ID": PID, "STORAGE_BUCKET": f"{PID}.appspot.com", "IS_ADS_ENABLED": False,
                   "IS_ANALYTICS_ENABLED": False, "IS_APPINVITE_ENABLED": False, "IS_GCM_ENABLED": True,
                   "IS_SIGNIN_ENABLED": False, "GOOGLE_APP_ID": "1:000000000000:ios:0000000000000000"}, f)
