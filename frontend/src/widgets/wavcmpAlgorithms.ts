// 파형비교 알고리즘 선택지 -- 오디오 파형비교 위젯과 테스트 실행기가
// 동일한 상세 표기를 공유한다.
export const WAVCMP_ALGORITHMS = [
  { value: 'comprehensive', label: '종합 (7지표 평균)' },
  { value: 'mfcc_dtw', label: 'MFCC-DTW (Shape)' },
  { value: 'fft', label: 'FFT 상관 (주파수)' },
  { value: 'fft_band', label: '대역 FFT 상관 (음성대역)' },
  { value: 'xcorr', label: '상호상관 (Shape)' },
  { value: 'rms', label: 'RMS (Amplitude)' },
  { value: 'zcr', label: 'ZCR' },
  { value: 'centroid', label: 'Centroid (주파수)' },
];
