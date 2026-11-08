import pytest
from unittest.mock import patch, MagicMock
from core.taufa_video_evaluator import TaufaVideoEvaluator


def test_educational_programming_video_evaluation_chatgpt_fallback():
    evaluator = TaufaVideoEvaluator()
    # Evaluate "Python in 100 Seconds" by Fireship with no transcript
    res = evaluator.evaluate_video(
        url="https://www.youtube.com/watch?v=x7X9w_Glm1s",
        video_id="x7X9w_Glm1s",
        title="Python in 100 Seconds",
        channel="Fireship",
        duration_seconds=143
    )

    assert res["decision"] == "EDUCATIONAL"
    assert res["video_id"] == "x7X9w_Glm1s"
    assert res["duration_seconds"] == 143
    assert "Python in 100 Seconds" in res["reason"]
    assert res["evaluator_source"] in ("CHATGPT_BRAVE_SESSION", "BRAVE_CHATGPT_CURRICULUM")
    assert res["confidence"] >= 0.85


def test_non_educational_entertainment_video_evaluation():
    evaluator = TaufaVideoEvaluator()
    res = evaluator._evaluate_via_curriculum_or_chatgpt(
        video_id="test1234",
        canonical_url="https://www.youtube.com/watch?v=test1234",
        title="Funny Cat Pranks and Comedy Fails compilation (official video)",
        channel="LaughHub",
        duration_sec=300,
        dur_formatted="05:00",
        clean_desc="hilarious comedy pranks and video game funny moments",
        chapters=[]
    )

    assert res["decision"] == "NOT_EDUCATIONAL"
    assert "entertainment" in res["reason"].lower() or "not_educational" in res["reason"].lower()


def test_chatgpt_session_info_detection():
    info = TaufaVideoEvaluator.get_chatgpt_session_info()
    assert isinstance(info, dict)
    assert "active" in info
    assert info["active"] is True
    assert info.get("email") is not None
