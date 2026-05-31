# KARB_REALTIME_V1 PLAN

기존 V3.0은 더 이상 메인으로 사용하지 않으며, 새로운 김프 실시간 양쪽매매 전용 프로젝트를 C:\KARB_REALTIME_V1에 구축합니다.

## 벤치마킹 기준
- Hummingbot Cross-Exchange Market Making 구조
- Hummingbot Arbitrage / Hedge 구조
- CCXT식 exchange abstraction
- 목표는 연구용 대량 데이터 분석이 아니라 실시간 김프 차익 계산 및 paper/tiny_live/live 운용 구조

## 규칙
- V3.0 폴더 수정 금지 / 파일 복사 금지
- 대량 raw tick, jsonl, 수천만 행 sqlite 저장 금지
- 처음부터 복잡한 백테스트/마이닝 구현 금지
- API key 하드코딩 금지
- Upbit/Binance public orderbook을 실시간 수신하여 계산
- Private 주문 모듈은 작성하되 기본 모드(paper)에서는 호출 금지

## 방향성 A (Upbit 고평가)
Upbit SELL / Binance BUY

## 방향성 B (Upbit 저평가)
Upbit BUY / Binance SELL
(단, Binance 재고 존재 시에만)
