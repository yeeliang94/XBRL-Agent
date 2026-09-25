import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic_ai.models.test import TestModel

from scout.prepared_map import DocumentMap, document_index, validate_document_map, build_prepared_document_map


def test_prepared_scout_retains_variant_selection_guidance():
    from scout.prepared_map import SYSTEM_PROMPT
    assert "CuNonCu when current/non-current sections OR totals appear" in SYSTEM_PROMPT
    assert "OrderOfLiquidity only when the source has no current/non-current split" in SYSTEM_PROMPT
    assert "Function groups expenses by role" in SYSTEM_PROMPT
    assert "Indirect" in SYSTEM_PROMPT and "profit with adjustments" in SYSTEM_PROMPT


def test_prepared_scout_states_exact_top_level_output_shape():
    from scout.prepared_map import SYSTEM_PROMPT

    prompt = " ".join(SYSTEM_PROMPT.split())
    assert "top-level output MUST contain `infopack` and `ownership_ranges`" in prompt
    assert "Put `notes_inventory` inside `infopack`, never at the top level" in prompt


@pytest.fixture
def prepared(tmp_path):
    metadata = tmp_path / 'preparation.json'
    metadata.write_text(json.dumps({'configuration_key': 'test', 'source_file': 'uploaded.pdf'}))
    return SimpleNamespace(metadata_path=metadata, revision='rev1', page_count=2,
        prepared_pdf_path=tmp_path/'prepared.pdf', rotation_corrections={}, blocks=[
            {'block_id': 'a', 'page': 1, 'block_kind': 'heading', 'canonical_html': '<h2>1. Policies</h2>', 'locator': {}},
            {'block_id': 'b', 'page': 1, 'block_kind': 'paragraph', 'canonical_html': '<p>Policy.</p>',
             'locator': {'capture_uncertain': True, 'heading_ancestor_ids': ['a']}},
            {'block_id': 'c', 'page': 2, 'block_kind': 'paragraph', 'canonical_html': '<p>Continued.</p>',
             'continues_block_id': 'b', 'locator': {}}])


def mapped():
    return {'infopack': {'toc_page': 1, 'page_offset': 0, 'statements': {}, 'notes_inventory': [
        {'note_num': 1, 'title': 'Policies', 'page_range': [1, 2]}]},
        'ownership_ranges': [{'first_block_id': 'a', 'last_block_id': 'c', 'owner_kind': 'note',
                              'source_note_id': 'policy', 'source_note_num': '1', 'source_note_title': 'Policies'}]}


def test_reason_schema_exposes_allowed_codes_before_mapping_validation():
    from pydantic import ValidationError
    result = mapped()
    result['ownership_ranges'][0].update(owner_kind='metadata', reason_code='PRIMARY_STATEMENTS')
    with pytest.raises(ValidationError, match='DOCUMENT_METADATA') as exc:
        DocumentMap.model_validate(result)
    assert exc.value.errors()[0]['loc'] == ('ownership_ranges', 0, 'reason_code')


def test_reason_repair_identifies_range_and_exact_allowed_value(prepared):
    result = mapped()
    result['ownership_ranges'][0].update(owner_kind='metadata', reason_code='PAGE_HEADER')
    with pytest.raises(ValueError, match=r'ownership_ranges\[0\].reason_code.*DOCUMENT_METADATA'):
        validate_document_map(prepared, DocumentMap.model_validate(result))


def test_complete_exact_range_expansion_and_uncertain_source_index(prepared):
    info, assignments = validate_document_map(prepared, DocumentMap.model_validate(mapped()))
    assert [a['block_id'] for a in assignments] == ['a', 'b', 'c']
    assert info.notes_inventory[0].note_num == 1
    assert document_index(prepared)[1]['locator']['capture_uncertain']


@pytest.mark.parametrize('furniture_first', [False, True])
def test_nested_furniture_is_an_explicit_exception_not_an_overlap_error(prepared, furniture_first):
    prepared.blocks[1]['canonical_html'] = '<p>1</p>'
    prepared.blocks[2].pop('continues_block_id')
    prepared.blocks[2]['locator'] = {}
    result = mapped()
    furniture = {'first_block_id': 'b', 'last_block_id': 'b',
                 'owner_kind': 'furniture', 'reason_code': 'PAGE_NUMBER'}
    result['ownership_ranges'].insert(0 if furniture_first else 1, furniture)
    _, assigned = validate_document_map(prepared, DocumentMap.model_validate(result))
    assert [a['owner_kind'] for a in assigned] == ['note', 'furniture', 'note']
    assert len({a['block_id'] for a in assigned}) == 3


def test_duplicate_identical_range_does_not_duplicate_source(prepared):
    result = mapped()
    result['ownership_ranges'].append(dict(result['ownership_ranges'][0]))
    _, assigned = validate_document_map(prepared, DocumentMap.model_validate(result))
    assert len(assigned) == 3


@pytest.mark.parametrize("reason", ["PAGE_HEADER", "PAGE_FOOTER", "PAGE_NUMBER"])
def test_substantive_one_off_prose_cannot_be_hidden_by_a_furniture_label(prepared, reason):
    result = mapped()
    result['ownership_ranges'].append({'first_block_id': 'b', 'last_block_id': 'b',
        'owner_kind': 'furniture', 'reason_code': reason})
    with pytest.raises(ValueError, match='Furniture exclusion.*retain and reassign'):
        validate_document_map(prepared, DocumentMap.model_validate(result))


def test_repeated_routine_footer_can_be_excluded(prepared):
    prepared.blocks[2].pop('continues_block_id')
    for bid, page in [('f1', 1), ('f2', 2)]:
        prepared.blocks.append({'block_id': bid, 'page': page, 'block_kind': 'paragraph',
            'canonical_html': '<p>Annual report</p>', 'locator': {}})
    result = mapped()
    result['ownership_ranges'].append({'first_block_id': 'f1', 'last_block_id': 'f2',
        'owner_kind': 'furniture', 'reason_code': 'PAGE_FOOTER'})
    _, assigned = validate_document_map(prepared, DocumentMap.model_validate(result))
    assert [item['owner_kind'] for item in assigned[-2:]] == ['furniture', 'furniture']


def test_nested_furniture_with_overlapping_identical_owners_is_order_independent(prepared):
    from itertools import permutations
    prepared.blocks[2].pop('continues_block_id')
    prepared.blocks[2]['locator'] = {}
    for block in prepared.blocks:
        block['canonical_html'] = '<p>Annual report</p>'
    result = mapped()
    broad = result['ownership_ranges'][0]
    narrow = {**broad, 'last_block_id': 'b'}
    furniture = {'first_block_id': 'a', 'last_block_id': 'b',
                 'owner_kind': 'furniture', 'reason_code': 'PAGE_HEADER'}
    for spans in permutations([broad, narrow, furniture]):
        result['ownership_ranges'] = list(spans)
        _, assigned = validate_document_map(prepared, DocumentMap.model_validate(result))
        assert [a['owner_kind'] for a in assigned] == ['furniture', 'furniture', 'note']


@pytest.mark.parametrize('change', ['gap', 'overlap', 'unknown', 'reverse', 'relationship', 'inventory'])
def test_invalid_maps_never_silently_publish(prepared, change):
    result = mapped()
    ranges = result['ownership_ranges']
    if change == 'gap': ranges[0]['last_block_id'] = 'b'
    if change == 'overlap': ranges.append({**ranges[0], 'source_note_id': 'different-note'})
    if change == 'unknown': ranges[0]['last_block_id'] = 'missing'
    if change == 'reverse': ranges[0].update(first_block_id='c', last_block_id='a')
    if change == 'relationship':
        ranges[0]['last_block_id'] = 'b'
        ranges.append({**ranges[0], 'first_block_id': 'c', 'last_block_id': 'c', 'source_note_id': 'other'})
    if change == 'inventory': result['infopack']['notes_inventory'][0]['note_num'] = 2
    with pytest.raises(ValueError):
        validate_document_map(prepared, DocumentMap.model_validate(result))


def test_supplied_inventory_is_exact_constraint(prepared):
    result = DocumentMap.model_validate(mapped())
    validate_document_map(prepared, result, inventory=mapped()['infopack'])
    with pytest.raises(ValueError, match='supplied'):
        validate_document_map(prepared, result, inventory={'notes_inventory': []})


def test_agent_map_usage_and_cache_without_paid_model(prepared, monkeypatch):
    import scout.agent
    monkeypatch.setattr(scout.agent, '_thinking_level_for', lambda role: None)
    model = TestModel(custom_output_args=mapped(), call_tools=[])
    usage = {}
    info, assignments = asyncio.run(build_prepared_document_map(prepared, model, usage_out=usage))
    assert info.notes_inventory and len(assignments) == 3
    assert usage['requests'] == 1
    assert (prepared.metadata_path.parent/'SCOUT_conversation_trace.json').exists()
    cached_usage = {}
    asyncio.run(build_prepared_document_map(prepared, model, usage_out=cached_usage))
    assert cached_usage['cache_hit'] and cached_usage['requests'] == 0


def test_cancellation_releases_shared_budget_and_records_failed_usage(prepared, monkeypatch):
    from contextlib import asynccontextmanager
    import ingest.document_preparation as capture
    import scout.agent
    import scout.prepared_map as mapper
    monkeypatch.setattr(scout.agent, '_thinking_level_for', lambda role: None)
    events = []
    class Budget:
        @asynccontextmanager
        async def slot(self):
            events.append('entered')
            try:
                yield
            finally:
                events.append('released')
    monkeypatch.setattr(capture, '_REQUEST_BUDGET', Budget())
    async def cancelled(self, *args, **kwargs):
        kwargs['usage'].input_tokens = 17
        kwargs['usage'].requests = 1
        raise asyncio.CancelledError()
    monkeypatch.setattr(mapper.Agent, 'run', cancelled)
    usage = {}
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(build_prepared_document_map(prepared, TestModel(), usage_out=usage))
    assert events == ['entered', 'released']
    assert usage['prompt_tokens'] == 17 and usage['turn_count'] == 1
    assert not list(prepared.metadata_path.parent.glob('prepared-map-*.json'))


def test_preview_does_not_duplicate_full_uncertainty_per_block(prepared):
    prepared.blocks[1]['locator']['uncertainties'] = [{'reason': 'x' * 10000}]
    assert len(json.dumps(document_index(prepared))) < 2000


def test_map_authors_table_caption_footnote_group_and_cached_revalidation_is_idempotent(prepared):
    from notes.source_models import SourceBlock
    from notes.source_write import expand_table_groups
    prepared.blocks = [
        {'block_id': 'a', 'page': 1, 'block_kind': 'paragraph', 'canonical_html': '<p>Credit risk exposure</p>', 'locator': {}},
        {'block_id': 'b', 'page': 1, 'block_kind': 'table', 'canonical_html': '<table><tr><td>100</td></tr></table>', 'locator': {}},
        {'block_id': 'c', 'page': 1, 'block_kind': 'paragraph', 'canonical_html': '<p>Amounts include guarantees.</p>', 'locator': {}},
    ]
    raw = mapped()
    raw['relationship_groups'] = [['a', 'b', 'c']]
    result = DocumentMap.model_validate(raw)
    _, assignments = validate_document_map(prepared, result)
    for block, assignment in zip(prepared.blocks, assignments):
        assert assignment['required_related_block_ids'] == [b['block_id'] for b in prepared.blocks if b is not block]
        block['locator']['required_related_block_ids'] = assignment['required_related_block_ids']
    _, cached_assignments = validate_document_map(prepared, result)
    assert cached_assignments == assignments
    blocks = [SourceBlock(b['block_id'], b['block_kind'], i, b['canonical_html'], locator=b['locator'])
              for i, b in enumerate(prepared.blocks)]
    assert expand_table_groups(blocks, ['b']) == ['a', 'b', 'c']
    assert expand_table_groups(blocks, ['c']) == ['a', 'b', 'c']


@pytest.mark.parametrize('group', [['a', 'missing'], ['a', 'a'], []])
def test_invalid_authored_relationship_groups_fail(prepared, group):
    raw = mapped()
    raw['relationship_groups'] = [group]
    with pytest.raises(ValueError, match='Relationship'):
        validate_document_map(prepared, DocumentMap.model_validate(raw))


def test_authored_relationship_group_cannot_cross_note_owners(prepared):
    prepared.blocks[2].pop('continues_block_id')
    raw = mapped()
    raw['ownership_ranges'][0]['last_block_id'] = 'b'
    raw['ownership_ranges'].append({**raw['ownership_ranges'][0], 'first_block_id': 'c',
                                    'last_block_id': 'c', 'source_note_id': 'other', 'source_note_num': ''})
    raw['relationship_groups'] = [['b', 'c']]
    with pytest.raises(ValueError, match='relationship conflicts'):
        validate_document_map(prepared, DocumentMap.model_validate(raw))


def test_corrected_capture_same_revision_invalidates_map_cache(prepared, monkeypatch):
    import scout.agent
    monkeypatch.setattr(scout.agent, '_thinking_level_for', lambda role: None)
    model = TestModel(custom_output_args=mapped(), call_tools=[])
    asyncio.run(build_prepared_document_map(prepared, model))
    prepared.blocks[1]['canonical_html'] = '<p>Corrected policy text.</p>'
    usage = {}
    asyncio.run(build_prepared_document_map(prepared, model, usage_out=usage))
    assert usage['requests'] == 1 and not usage.get('cache_hit')


def test_metadata_only_map_cannot_report_financial_document_success(prepared):
    raw = mapped()
    raw['infopack']['notes_inventory'] = []
    raw['ownership_ranges'] = [{'first_block_id': 'a', 'last_block_id': 'c',
                                'owner_kind': 'metadata', 'reason_code': 'DOCUMENT_METADATA'}]
    with pytest.raises(ValueError, match='metadata-only'):
        validate_document_map(prepared, DocumentMap.model_validate(raw))


def test_fresh_map_rejects_bare_string_face_line_refs():
    """The historical Infopack loader may drop malformed persisted entries,
    but newly generated maps must fail before cache activation.
    """
    raw = mapped()
    raw["infopack"]["statements"] = {
        "SOFP": {
            "variant_suggestion": "CuNonCu",
            "face_page": 1,
            "note_pages": [2],
            "confidence": "HIGH",
            "face_line_refs": ["p1b3"],
            "face_read_in_detail": True,
        }
    }

    with pytest.raises(ValueError, match="face_line_refs"):
        DocumentMap.model_validate(raw)


def test_fresh_map_preserves_typed_face_line_refs(prepared):
    raw = mapped()
    raw["infopack"]["statements"] = {
        "SOFP": {
            "variant_suggestion": "CuNonCu",
            "face_page": 1,
            "note_pages": [2],
            "confidence": "HIGH",
            "face_line_refs": [{
                "label": "Property, plant and equipment",
                "note_num": 4,
                "section": "non-current assets",
            }],
            "face_read_in_detail": True,
        }
    }

    info, _ = validate_document_map(
        prepared, DocumentMap.model_validate(raw),
    )

    assert info.statements[next(iter(info.statements))].face_line_refs[0].label == (
        "Property, plant and equipment"
    )


def test_singleton_group_is_a_noop_and_standard_case_is_preserved(prepared):
    raw = mapped()
    raw['relationship_groups'] = [['b']]
    raw['infopack']['detected_standard'] = 'MFRS'
    info, assignments = validate_document_map(prepared, DocumentMap.model_validate(raw))
    assert info.detected_standard == 'mfrs'
    assert not assignments[1]['required_related_block_ids']


def test_retry_can_correct_a_mistaken_note_identity(prepared, monkeypatch):
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    import scout.agent
    monkeypatch.setattr(scout.agent, '_thinking_level_for', lambda role: None)
    attempts = []
    def respond(messages, info):
        attempts.append(messages)
        raw = mapped()
        if len(attempts) == 1:
            raw['infopack']['notes_inventory'][0]['note_num'] = 2
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, raw)])
    info, assignments = asyncio.run(build_prepared_document_map(prepared, FunctionModel(respond)))
    assert len(attempts) == 2
    assert info.notes_inventory[0].note_num == 1
    assert all(a['owner_kind'] == 'note' for a in assignments)


def test_repeated_malformed_map_has_actionable_preparation_error(prepared, monkeypatch):
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from ingest.document_preparation import PreparationError
    import scout.agent

    monkeypatch.setattr(scout.agent, '_thinking_level_for', lambda role: None)
    attempts = []

    def respond(messages, info):
        attempts.append(messages)
        malformed = {
            'toc_page': 1,
            'page_offset': 0,
            'statements': {},
            'notes_inventory': [],
        }
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, malformed)])

    with pytest.raises(
        PreparationError,
        match='AI service returned an invalid document map after 3 attempts',
    ):
        asyncio.run(build_prepared_document_map(prepared, FunctionModel(respond)))

    assert len(attempts) == 3
    assert (prepared.metadata_path.parent / 'SCOUT_conversation_trace.json').exists()


def test_unknown_range_names_the_field_and_bad_identifier(prepared):
    raw = mapped()
    raw['ownership_ranges'][0]['last_block_id'] = 'stale-block'
    with pytest.raises(ValueError, match=r'ownership_ranges\[0\].last_block_id.*stale-block'):
        validate_document_map(prepared, DocumentMap.model_validate(raw))


def test_missing_notes_inventory_gets_a_short_named_repair_message(prepared, monkeypatch):
    """Trace audit (2026-09-25): 5 of 19 scout runs omitted notes_inventory and
    the schema error echoed the whole ~12k-char map back. The repair message
    now names the missing field without repeating the map."""
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart
    import scout.agent
    monkeypatch.setattr(scout.agent, '_thinking_level_for', lambda role: None)
    attempts = []

    def respond(messages, info):
        attempts.append(messages)
        raw = mapped()
        if len(attempts) == 1:
            del raw['infopack']['notes_inventory']
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, raw)])

    info, _ = asyncio.run(build_prepared_document_map(prepared, FunctionModel(respond)))
    assert len(attempts) == 2
    retry = [p for m in attempts[1] for p in m.parts if isinstance(p, RetryPromptPart)]
    text = retry[-1].model_response()
    assert "missing required field(s): infopack.notes_inventory" in text
    assert len(text) < 1000
    assert info.notes_inventory
