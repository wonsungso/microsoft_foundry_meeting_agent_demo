# Hosted Agent Service Package

이 디렉터리는 다섯 Microsoft Foundry Hosted Agent가 공유하는 Python 배포 package입니다. 전체 아키텍처, Agent별 LLM 사용 여부, source 설명, 배포 및 Trace 방법은 [프로젝트 README](../../README.md)를 참고합니다.

## Triage 요청 payload

회의실 분석:

```json
{
  "tool": "analyze_room",
  "ocr_texts": ["회의실", "AI Glasses 운영 리뷰"]
}
```

회의 종료:

```json
{
  "tool": "summarize_meeting",
  "transcript": "회의 전체 transcript",
  "meeting": {
    "title": "AI Glasses 엣지 런타임 운영 리뷰",
    "attendees": 5
  },
  "frames": []
}
```

외부 client는 `triage-agent`의 Responses endpoint만 호출합니다. Triage가 `context-agent`, `meeting-agent`, `summarizer-agent`, `notify-agent`를 원격으로 조정합니다.
