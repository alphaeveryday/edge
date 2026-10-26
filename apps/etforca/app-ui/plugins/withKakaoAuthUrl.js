const { withAppDelegate } = require('expo/config-plugins');

// 카카오 로그인 플러그인이 AppDelegate 에 넣는 RNKakaoLogins 참조를 카카오 SDK 공개 API 호출로 교체
// RNKakaoLogins 는 비공개 Swift 클래스라 dynamic frameworks 에서 앱이 링크하지 못함
const FROM = 'if kakao_login.RNKakaoLogins.isKakaoTalkLoginUrl(url) { return kakao_login.RNKakaoLogins.handleOpen(url) }';
const TO = 'if AuthApi.isKakaoTalkLoginUrl(url) { return AuthController.handleOpenUrl(url: url) }';

module.exports = (config) =>
  withAppDelegate(config, (c) => {
    let s = c.modResults.contents;
    if (!s.includes(FROM)) throw new Error('withKakaoAuthUrl: 카카오 플러그인의 AppDelegate 주입이 먼저 실행돼야 함');
    s = s.replace('import kakao_login\n', 'import KakaoSDKAuth\n').replace(FROM, TO);
    if (s.includes('kakao_login')) throw new Error('withKakaoAuthUrl: kakao_login 참조가 남아 있음');
    c.modResults.contents = s;
    return c;
  });
