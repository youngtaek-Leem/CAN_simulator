// 앱 버전 단일 소스 -- 여기만 바꾸면 상단 로고(App.tsx)에 반영된다.
// 브라우저 탭 제목(index.html의 <title>)은 빌드 전 정적 HTML이라 이 값을
// 직접 참조하지 못하므로, 버전을 바꿀 때는 index.html의 <title>도 같이
// 맞춰서 바꿔야 한다(그쪽에도 안내 주석 있음).
export const APP_VERSION = 'Rev 1.0';
