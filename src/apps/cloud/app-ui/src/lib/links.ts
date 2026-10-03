import * as Linking from 'expo-linking';

// app-api 정적 페이지인 약관과 처리방침
const SITE = (process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8080/api/v1').replace(/\/api\/v1$/, '');

export const openTerms = () => Linking.openURL(`${SITE}/terms.html`);
export const openPrivacy = () => Linking.openURL(`${SITE}/privacy.html`);
