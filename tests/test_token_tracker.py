from token_tracker import TokenReport, TurnRecord


def test_add_turn():
    report = TokenReport()
    record = TurnRecord(
        turn=1,
        tool_name="view_pdf_pages",
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
        thinking_tokens=100,
        cumulative_tokens=1500,
        duration_ms=3000,
        timestamp=0,
    )
    report.add_turn(record)
    assert len(report.turns) == 1
    assert report.total_prompt_tokens == 1000
    assert report.total_completion_tokens == 500


def test_cumulative_tokens():
    """grand_total includes thinking tokens; add_turn populates
    cumulative_tokens from the running totals (peer-review I15)."""
    report = TokenReport()
    report.add_turn(
        TurnRecord(
            turn=1,
            tool_name="read_template",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            thinking_tokens=0,
            cumulative_tokens=0,  # add_turn overwrites this
            duration_ms=100,
            timestamp=0,
        )
    )
    report.add_turn(
        TurnRecord(
            turn=2,
            tool_name="view_pdf_pages",
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            thinking_tokens=100,
            cumulative_tokens=0,  # add_turn overwrites this
            duration_ms=3000,
            timestamp=0,
        )
    )
    assert report.total_prompt_tokens == 1100
    assert report.total_completion_tokens == 550
    assert report.total_thinking_tokens == 100
    # 1100 prompt + 550 completion + 100 thinking = 1750
    assert report.grand_total == 1750
    # cumulative_tokens on each turn reflects the running grand total
    assert report.turns[0].cumulative_tokens == 150
    assert report.turns[1].cumulative_tokens == 1750


def test_format_table():
    report = TokenReport()
    report.add_turn(
        TurnRecord(
            turn=1,
            tool_name="read_template",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            thinking_tokens=0,
            cumulative_tokens=150,
            duration_ms=100,
            timestamp=0,
        )
    )
    table = report.format_table()
    assert "read_template" in table
    assert "Total" in table
    assert "Estimated cost" in table


def test_estimate_cost():
    report = TokenReport(model="vertex_ai.gemini-3-flash-preview")
    report.add_turn(
        TurnRecord(
            turn=1,
            tool_name="view_pdf_pages",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            total_tokens=2_000_000,
            thinking_tokens=0,
            cumulative_tokens=2_000_000,
            duration_ms=5000,
            timestamp=0,
        )
    )
    cost = report.estimate_cost()
    # Gemini 3 Flash: $0.50/MTok input + $3.00/MTok output = $3.50 for 1M+1M tokens
    assert cost == 3.50


def test_thinking_tokens_priced_at_output_rate():
    """Peer-review C5: Claude extended thinking and OpenAI reasoning tokens
    are billed as output by the provider. The old code charged them at the
    input rate, materially understating Claude/GPT-5 costs."""
    report = TokenReport(model="bedrock.anthropic.claude-sonnet-4-6")
    report.add_turn(
        TurnRecord(
            turn=1,
            tool_name="view_pdf_pages",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            thinking_tokens=1_000_000,
            cumulative_tokens=1_000_000,
            duration_ms=5000,
            timestamp=0,
        )
    )
    cost = report.estimate_cost()
    # Claude Sonnet 4.6: $15/MTok output. 1M thinking tokens → $15, not $3
    # (the old input-rate price).
    assert cost == 15.0
