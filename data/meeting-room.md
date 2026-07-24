# AI Glasses 엣지 런타임 운영 리뷰

- **일시**: 2026-07-13 (월) 10:00 ~ 11:10
- **장소**: 회의실
- **참석자**: 홍길동(팀장), 김철수, 이영희, 박민수, 최영희
- **회의 목적**: AI Glasses의 위치 추정 이벤트를 엣지에서 안정적으로 처리하고 Azure IoT Hub까지 전달하는 운영 설계 확정

## 관측 데이터와 위치 추정
AI Glasses는 visual-inertial odometry로 계산한 3차원 위치 벡터와 orientation quaternion을 5초 간격으로 엣지 Hub에 전송합니다. 좌표는 `office-floor-1` 로컬 좌표계를 사용하며, 회의실 진입 판정은 다각형 geofence와 신뢰도 0.90 이상 조건을 함께 적용합니다. 단일 프레임의 위치 점프를 방지하기 위해 최근 5개 벡터의 이동 평균과 IMU 품질 지표를 사용합니다.

## 엣지 런타임과 장애 대응
1. Glasses 연결이 끊기면 엣지 Hub가 최대 15분 동안 telemetry를 로컬 queue에 보관한 뒤 순서대로 재전송합니다.
2. Active-Passive Hub failover 시 device twin의 checkpoint와 마지막 sequence number를 복제해 중복 이벤트를 억제합니다.
3. 위치 이벤트의 종단 간 latency 목표는 P95 800ms, 유실률은 0.1% 미만으로 정의합니다.
4. Azure IoT Hub throttling 발생 시 exponential backoff와 jitter를 적용하고 dead-letter 항목을 운영 대시보드에 노출합니다.

## 실험 결과
| 항목 | 기준 | 측정값 | 판정 |
|---|---:|---:|---|
| 회의실 진입 정확도 | 95% 이상 | 97.8% | 통과 |
| 위치 이벤트 P95 latency | 800ms 이하 | 612ms | 통과 |
| Hub 전환 복구 시간 | 10초 이하 | 8.4초 | 통과 |
| 오프라인 재전송 유실률 | 0.1% 미만 | 0.04% | 통과 |

## 결정 사항
- 위치 원본 벡터, 좌표계, quaternion, confidence를 IoT 메시지에 함께 저장합니다.
- Cloud에는 영상 프레임을 전송하지 않고 위치 telemetry와 회의 공간 식별 결과만 전달합니다.
- 배포 ring을 Lab → Pilot → Production으로 분리하고 device twin desired property로 런타임 버전을 제어합니다.

## Action Item
- [ ] 김철수: Hub failover 부하 테스트와 sequence 중복률 보고서를 작성하세요. (~07/16)
- [ ] 이영희: 위치 telemetry latency 및 queue depth 대시보드를 구성하세요. (~07/15)
- [ ] 박민수: Pilot Glasses 20대의 device twin rollout을 리허설하세요. (~07/17)
- [ ] 최영희: geofence 경계 구간 오탐 데이터셋을 정리하세요. (~07/18)
