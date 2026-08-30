from types import SimpleNamespace

from usage_metrics import derive_thinking_tokens, split_usage


def test_split_usage_separates_openai_reasoning_from_visible_completion():
    metrics = split_usage(SimpleNamespace(
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        details={"reasoning_tokens": 30},
    ))

    assert metrics.prompt_tokens == 100
    assert metrics.completion_tokens == 20
    assert metrics.thinking_tokens == 30
    assert metrics.total_tokens == 150


def test_split_usage_supports_google_thought_tokens_and_legacy_names():
    metrics = split_usage(SimpleNamespace(
        request_tokens=80,
        response_tokens=25,
        total_tokens=105,
        details={"thoughts_tokens": 15},
    ))

    assert metrics.prompt_tokens == 80
    assert metrics.completion_tokens == 10
    assert metrics.thinking_tokens == 15


def test_split_usage_clamps_bad_provider_reasoning_data():
    metrics = split_usage(SimpleNamespace(
        input_tokens=40,
        output_tokens=12,
        total_tokens=52,
        details={"reasoning_tokens": 99},
    ))

    assert metrics.completion_tokens == 0
    assert metrics.thinking_tokens == 12
    assert metrics.total_tokens == 52


def test_derive_thinking_tokens_supports_new_and_legacy_rows():
    assert derive_thinking_tokens(150, 100, 20) == 30
    assert derive_thinking_tokens(150, 100, 50) == 0
    assert derive_thinking_tokens(100, 80, 30) == 0
