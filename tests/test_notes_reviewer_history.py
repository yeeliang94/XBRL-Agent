"""Old notes reads can be reloaded without carrying their full text forever."""
import copy

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolReturnPart

from notes.reviewer_history import compact_stale_notes_reads


def _returned(name: str, content: str, index: int):
    return ModelRequest(parts=[ToolReturnPart(
        tool_name=name, content=content, tool_call_id=f"call-{index}",
    )])


def test_compacts_old_source_reads_and_preserves_re_read_locators():
    messages = []
    for index in range(5):
        messages.append(_returned(
            "read_source_manifest",
            f"Note {index + 1} has parts.\np12-b{index}-abc123 " + "source wording " * 500,
            index,
        ))
        messages.append(ModelResponse(parts=[TextPart("next")]))
    messages.append(_returned("edit_note_cells", "ok: write survived", 9))
    before = copy.deepcopy(messages)

    result = compact_stale_notes_reads(messages)

    assert result is not messages
    oldest = result[0].parts[0].content
    assert "p12-b0-abc123" in oldest
    assert "Re-read" in oldest
    assert "source wording " not in oldest
    # The newest source reads and every write acknowledgement remain verbatim.
    assert result[6].parts[0].content == before[6].parts[0].content
    assert result[8].parts[0].content == before[8].parts[0].content
    assert result[-1].parts[0].content == "ok: write survived"
    assert messages == before
