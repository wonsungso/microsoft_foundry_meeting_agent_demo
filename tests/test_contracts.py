import pytest
from meeting_agent.contracts import AnalyzeRoomRequest, SummarizeMeetingRequest, parse_request
from pydantic import ValidationError


def test_parse_analyze_room_contract() -> None:
    request = parse_request('{"tool":"analyze_room","ocr_texts":["회의실 11A-04"]}')

    assert isinstance(request, AnalyzeRoomRequest)
    assert request.ocr_texts == ["회의실 11A-04"]


def test_parse_summarize_meeting_contract() -> None:
    request = parse_request(
        {
            "tool": "summarize_meeting",
            "transcript": "결정: 데모를 진행한다.",
            "meeting": {"title": "MS미팅", "attendees": 2},
            "frames": ["https://example.com/whiteboard.jpg"],
        }
    )

    assert isinstance(request, SummarizeMeetingRequest)
    assert request.meeting is not None
    assert request.meeting.title == "MS미팅"


def test_reject_non_http_frame_url() -> None:
    with pytest.raises(ValidationError):
        parse_request(
            {
                "tool": "summarize_meeting",
                "transcript": "기록 없음",
                "frames": ["file:///tmp/frame.jpg"],
            }
        )
