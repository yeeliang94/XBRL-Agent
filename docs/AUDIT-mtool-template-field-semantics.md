# Audit: mTool Template Field Semantics and Filing Readiness

Status: Implemented; independent code review and Windows mTool acceptance pending

Branch reviewed: `codex/mtool-socie-audit`

Audit date: 2026-08-27

Implementation status: Implemented on `codex/mtool-field-semantics`

Evidence status: The versioned registry, audit command, and reviewed snapshot
are committed. Run `python scripts/audit_template_field_semantics.py --check`.

Implementation note (2026-08-28): schema v41 separates taxonomy capability
from template slot role; extraction, review, writers, preflight, receipts, and
the current filing UI consume the same contract. The two MFRS numeric-note
wrapper omissions are named reviewed exceptions. This does not claim external
Windows mTool 2.2 acceptance; that final acceptance step remains outstanding.

## Executive conclusion

The audited `codex/mtool-socie-audit` branch did not resolve the defect where
structural headings could be presented to an AI agent as writable fields. It
also did not provide a
complete or sufficiently strict semantic foundation for filing-grade mTool or
XBRL generation.

The central problem is that the current `kind` classification combines three
independent questions:

1. Can the SSM taxonomy concept carry an XBRL fact?
2. Is this physical workbook location writable?
3. What shape or type of value belongs there?

Those questions must be represented and validated separately. A taxonomy
concept can have an XBRL element ID, labels, and documentation while still being
an abstract presentation node that cannot carry a fact. Conversely, a
non-abstract concept can appear in a workbook location that is presentation-only
or formula-controlled.

Two decisions must be made separately:

1. **Immediate defect containment:** stop headings from being exposed or
   accepted as writable targets, including at downstream write boundaries.
2. **Filing-readiness program:** build the complete semantic identity,
   manifest, migration, and acceptance system needed for canonical-to-mTool and
   future XBRL filing.

The immediate defect does not require completion of the whole filing-readiness
program. The recommended long-term solution remains a versioned,
variant-specific template manifest backed by an authoritative SSM taxonomy
concept registry.

## Scope

The audit covered the active template families for:

- MFRS and MPERS;
- Company and Group;
- numeric face statements;
- numeric supplementary-note templates;
- prose or text-block note templates;
- canonical fact and notes persistence;
- agent target discovery and workbook writers;
- review and edit APIs;
- mTool export, template mapping, preflight, receipt, and reverse ingestion.

Inventory examined:

- 58 active workbooks;
- 74 worksheets;
- 6,356 numeric nodes;
- 688 prose-note nodes.

Numeric parser classifications in the active variants:

| Current classification | Count |
| --- | ---: |
| `ABSTRACT` | 910 |
| `COMPUTED` | 984 |
| `LEAF` | 3,646 |
| `MATRIX_CELL` | 816 |
| Total | 6,356 |

Prose-note parser classifications:

| Current classification | Count |
| --- | ---: |
| `ABSTRACT` | 14 |
| `LEAF` | 674 |
| Total | 688 |

## Decision split and rough cost

These are order-of-magnitude planning estimates, not delivery commitments. They
assume one engineer familiar with this repository and exclude delays obtaining
genuine Windows mTool fixtures.

| Decision | Scope | Rough cost | Exit condition |
| --- | --- | --- | --- |
| A. Immediate containment | Reproducible probe, authoritative structural classification where mappings exist, preserve existing workbook presentation guards, reject non-writable targets at agent/writer/canonical/mTool seams, focused migration report | Medium, approximately 4–7 engineering days | No structural heading is offered or accepted across any active template; existing valid extraction behaviour remains intact |
| B. Filing-readiness program | Full taxonomy registry, variant manifests, complete prose/numeric-note mappings, canonical filing contexts, receipts, reverse ingest, migrations, and Windows acceptance | Large, approximately 4–8 engineering weeks plus external acceptance access | Every enabled variant satisfies the completeness guarantee and passes genuine mTool Validate/Generate inspection |

Option A should land first. It must not be implemented as "replace workbook
style with `abstract`" alone. The four reverse mismatches prove that workbook
slot role remains independently necessary, and dimensions/members require more
than an `abstract=false` check.

## Terminology

The implementation and user interface should stop using "XBRL field" for every
taxonomy concept.

- **Taxonomy concept:** An element defined by the SSM taxonomy. It may be a
  reportable primary item, abstract presentation node, dimension, hypercube, or
  member.
- **Reportable concept:** A primary item permitted to carry an XBRL fact.
- **Presentation node:** A concept or workbook row used for hierarchy or
  contextual display and not for data entry.
- **Template slot:** A physical location in a particular workbook variant.
- **Writable slot:** A template slot into which extraction, review, or editing
  may place a value.
- **Canonical fact:** A value associated with a reportable concept and complete
  filing context, unit, dimensions, and provenance.

## Confirmed defect

The SSM taxonomy declares structural headings as concepts. For example,
`FinancialReportingStatusAbstract` has an element ID and labels, but it is
declared with `abstract="true"`. Its child explanation concepts are
non-abstract and are the reportable fields.

This means the following implication is invalid:

```text
has an SSM/XBRL concept ID => writable field
```

The correct test is closer to:

```text
taxonomy role is reportable primary item
AND taxonomy abstract is false
AND selected template slot is writable input
```

The current parser derives `kind` primarily from workbook formatting and style
heuristics. It does not treat authoritative taxonomy metadata as the source of
truth. Downstream writers and exporters then trust that classification.

### Cross-template evidence

The comparison between current parser classification and SSM taxonomy
declarations found 30 mismatches:

- 26 taxonomy-abstract numeric concepts were classified as non-abstract by the
  parser. These are the dangerous false-writable cases.
- 4 non-abstract taxonomy concepts were classified as workbook abstracts. These
  are not automatically errors: a fact-capable concept may legitimately be used
  in a presentation-only workbook row. They prove that taxonomy capability and
  workbook writability are separate axes.

Deterministic probes confirmed:

- 26 of 26 misclassified numeric taxonomy abstracts were accepted by the
  numeric writer;
- all 20 identified prose taxonomy abstracts were exposed in the notes agent
  catalogue and accepted by the notes writer;
- all 60 identified header candidates in numeric-note templates were exposed
  and accepted by the notes writer;
- an incorrectly classified numeric heading could be persisted as a canonical
  fact and emitted as an mTool write;
- a prose heading could be persisted in `notes_cells` and emitted as an mTool
  footnote.

The 20 and 60 counts represent different audits and overlap with other
classifications. They must not be summed as a count of unique defects.

### Risk sizing and observed incidence

The 26 dangerous numeric mismatches are 0.71% of the 3,646 nodes currently
classified as numeric `LEAF`. That is a useful measure of static prevalence,
but not of run frequency or impact. A small number of filing destinations can
still have high compliance impact.

A read-only check of the locally available development database found:

- 268 runs;
- 4,180 canonical numeric fact rows;
- 396 persisted prose-note cells;
- two non-empty writes to `Notes-CI` row 6, `Financial reporting status`;
- one of those runs is `completed` and one is `failed`;
- one canonical numeric fact row associated with one of the 26 bad numeric
  nodes, but its value is `NULL`;
- zero non-null canonical numeric values on the 26 bad nodes;
- zero mTool fill receipts.

This is direct evidence that the prose defect has occurred in real local runs.
It is not evidence that an mTool artifact containing the bad value was produced,
and the local database must not be treated as a representative production
sample. Production incidence remains unknown until deployed databases and
receipts are audited.

### Reproducibility limitation

The original counts were produced by deterministic inline probes, not by a
committed script. That is a material evidence gap. An independent reviewer can
verify individual claims from the templates and taxonomy, but cannot currently
re-run one canonical command to reproduce the complete inventory and
writer-acceptance results.

Phase 0 must add:

- `scripts/audit_template_field_semantics.py` as a read-only, unattended probe;
- a machine-readable result snapshot containing template fingerprints and
  per-target findings;
- a focused test that fails while the current acceptance defect exists and
  goes green after containment;
- textual fixture identifiers and coordinates for the screenshot examples, so
  repository evidence does not depend on images attached only to a chat.

## Current failure paths

### Numeric statements

1. The parser uses workbook presentation styles to derive `kind`.
2. The extraction agent presents parser non-abstract nodes as data-entry
   targets.
3. The workbook writer rejects only rows recognized by the same style-derived
   header detector.
4. The facts API rejects only nodes already marked `ABSTRACT`.
5. The mTool exporter includes `LEAF` and `MATRIX_CELL` facts and therefore
   inherits any earlier misclassification.

Relevant implementation:

- `concept_model/parser.py`
- `tools/section_headers.py`
- `extraction/agent.py`
- `tools/fill_workbook.py`
- `concept_model/facts_api.py`
- `concept_model/cell_resolver.py`
- `concept_model/label_resolver.py`
- `mtool/exporter.py`

### Numeric notes

Numeric-note labels are discovered through the notes catalogue, which includes
every non-empty label in column A. The notes writer does not centrally validate
that the selected target is a writable canonical node before changing the
scratch workbook.

Canonical projection is best-effort. A canonical rejection does not necessarily
undo or reject the scratch-workbook write. This permits the scratch workbook and
canonical database to diverge while the run appears successful.

Relevant implementation:

- `notes/agent.py`
- `notes/writer.py`
- `notes/coordinator.py`
- `concept_model/facts_api.py`

### Prose notes

`notes_nodes` contains a `kind`, but the agent catalogue and writer do not
consistently use it as an eligibility boundary. Persistence accepts written
cells without requiring a valid writable `notes_nodes` target.

Existing invalid cells are deliberately surfaced by the API so users do not
lose content. That is useful for recovery, but they currently remain too close
to the ordinary editable/exportable path. Existing invalid content should be
quarantined rather than silently deleted.

Relevant implementation:

- `concept_model/notes_parser.py`
- `notes/agent.py`
- `notes/writer.py`
- `notes/persistence.py`
- `concept_model/facts_api.py`
- `api/notes.py`
- `mtool/notes_exporter.py`

### Tests currently pin the wrong behavior

`Financial reporting status` appears 53 times across 11 test files. Several of
those tests use it as writable fixture data, not merely as a negative example.
The parser tests compare parser output with the same style detector used by the
parser, so they cannot identify disagreement with the SSM taxonomy.

The replacement tests must be based on externally observable taxonomy and
template contracts rather than on the current implementation heuristic.

## Assessment of pending semantic-address work

The branch adds useful semantic-address and mTool mapping groundwork, but it
does not independently establish reportability or writability.

Current numeric semantic coverage is 6,206 of 6,356 nodes, leaving 150 without
semantic addresses.

Important uncovered variants include:

| Variant | Current semantic coverage |
| --- | ---: |
| MFRS Issued Capital, Company | 0 / 33 |
| MFRS Issued Capital, Group | 0 / 33 |
| MFRS Related Party, Company | 0 / 35 |
| MFRS Related Party, Group | 0 / 35 |

The current mapper also has these risks:

- linear mappings depend on row counts and ordering;
- a one-title-row tolerance can conceal alignment drift;
- SOCIE dimensions are partly hard-coded;
- prose-note taxonomy roles are not mapped into `notes_nodes`;
- uploaded templates can be searched for arbitrary strings matching a concept
  ID;
- generated target hints and legacy label fallback can bypass a verified
  coordinate-to-concept mapping;
- missing semantic addresses do not consistently block filing-grade export;
- the stored `primary_concept` is generally an XML schema element ID, not a
  namespace-qualified XBRL concept identity.

Therefore, a green mTool export currently does not prove that every selected
fact was mapped to a valid reportable SSM concept and the correct physical
destination.

## Existing safeguards versus new work

The plan must extend the controls already present rather than describe them as
new work.

| Concern | Current state on this branch | Additional work required |
| --- | --- | --- |
| Feature exposure | Explicit product decision: no `XBRL_MTOOL_FILL` exposure flag; action remains visible | Preserve this decision |
| Conflicts and review state | Preflight blocks relevant conflicts, incomplete face agents, reviewer flags, notes coverage, and source-integrity failures; written override is recorded | Add invalid-target and semantic-completeness findings to the same operator-visible model |
| Formula safety | Existing patcher refuses formula overwrite | No replacement required |
| Label matching | Machine docs use strict matching; degraded cases are reported | Filing-ready classification must not rely on label fallback |
| Template family and ambiguous mapping | Pending branch rejects family mismatch and unresolved/ambiguous numeric mappings | Validate reportability and slot writability before mapping; cover prose notes too |
| Fingerprints | Fingerprints and known-template descriptions exist and receipts record a fingerprint | Bind an accepted fingerprint to a versioned slot manifest and taxonomy version |
| Report before file | Existing artifact/report and degraded-download acknowledgement flow | Add semantic exclusions and readiness classification to the report/receipt |
| Abstract/reportable concept role | Not independently enforced | New authoritative taxonomy validation |
| Prose target eligibility | `notes_cells` may bypass `notes_nodes` | New write-time registry enforcement and legacy recovery |
| Filing context and QName | Current semantic address is partial | New canonical filing identity and context model |
| Reverse-ingest validity | Primarily trusts current concept catalogue/kind | New concept-role, slot, and semantic-completeness validation |

### Recorded no-exposure-gate decision

`CLAUDE.md`, backend tests, and frontend tests explicitly pin the 2026-08-05
product decision that mTool fill has no feature exposure gate. Safety currently
comes from preflight, reporting, acknowledgement, and receipts.

This audit does not recommend hiding the feature or restoring that flag. It
does propose a new distinction between:

- **technically unresolvable writes**, which must never be guessed into a cell;
- **operator-review blockers**, which follow the existing written-override
  policy;
- **degraded compatibility output**, which may remain downloadable after an
  acknowledgement but must not be represented as filing-ready.

Whether a semantic-invalidity blocker may be overridden by omitting the invalid
fact, or must withhold the whole artifact, is a product decision that must be
made explicitly. The current pending branch already makes unresolved or
ambiguous numeric mappings a non-overrideable HTTP 422, while ordinary preflight
blockers are overrideable. The implementation plan must reconcile that existing
split rather than introduce another unnamed gate.

## Proposed model

### Taxonomy concept registry

Add a versioned registry containing at least:

- taxonomy version;
- namespace URI;
- local name;
- SSM source element ID;
- abstract flag;
- substitution group or concept role;
- data type;
- period type;
- balance;
- labels and documentation where needed for display.

The registry must distinguish primary items from dimensions, hypercubes, and
members. Checking only `abstract=false` is insufficient.

### Variant-specific template-slot manifest

For each standard, entity level, statement variant, and workbook version, store:

- template identifier and workbook fingerprint;
- sheet and physical coordinate or canonical node identity;
- slot role;
- expected value/content kind;
- taxonomy-concept reference where applicable;
- dimensions and members;
- mapping source and version;
- validation status.

Suggested slot roles:

- `PRESENTATION_ONLY`
- `INPUT`
- `FORMULA`
- `MATRIX_INPUT`
- `MATRIX_FORMULA`
- `PERIOD_METADATA`

Do not add a separate manually maintained `fillable` flag. Derive eligibility
from taxonomy capability and slot role so the two cannot drift.

Current classifications cannot be mechanically renamed into the new roles:

| Current `kind` | Candidate slot roles | Migration rule |
| --- | --- | --- |
| `ABSTRACT` | Usually `PRESENTATION_ONLY` | Preserve as non-writable even when the associated taxonomy concept is non-abstract |
| `LEAF` | `INPUT` or `PRESENTATION_ONLY` | Require taxonomy primary-item capability and a writable workbook slot; the 26 false leaves must become presentation-only |
| `COMPUTED` | Usually `FORMULA` | Preserve reportable concept identity where applicable, but do not expose the slot for writes |
| `MATRIX_CELL` | `MATRIX_INPUT` or `MATRIX_FORMULA` | Derive dimensions and formula dependency status before allowing writes |
| Period/header metadata outside the model | `PERIOD_METADATA` or `PRESENTATION_ONLY` | Import as template metadata, not as canonical facts |

The four known reverse mismatches that prevent a taxonomy-only migration are:

- MFRS Company SOFP Order of Liquidity, row 52, `Biological assets`;
- MFRS Group SOFP Order of Liquidity, row 52, `Biological assets`;
- MFRS Company SOFP Order of Liquidity, row 134, `Inventories`;
- MFRS Group SOFP Order of Liquidity, row 134, `Inventories`.

These are non-abstract taxonomy concepts used in workbook rows currently treated
as presentation-only. Taxonomy-only classification would wrongly make them
writable; it would not wrongly block them.

| Taxonomy capability | Template slot role | Writable? |
| --- | --- | --- |
| Abstract or structural | Presentation-only | No |
| Reportable primary item | Presentation-only | No |
| Reportable primary item | Input | Yes |
| Reportable primary item | Formula | No |
| Dimension, hypercube, or member | Any | No primary fact |

### Shared enforcement module

Create one deep module, tentatively `FilingTargetRegistry`, with a narrow
interface:

```text
list_writable_targets(template_id)
resolve_write(template_id, target_id, purpose)
resolve_filing_address(canonical_fact)
audit_template(template_id)
```

The module owns:

- taxonomy lookup;
- manifest loading and fingerprint validation;
- reportability decisions;
- slot-writability decisions;
- semantic-address validation;
- diagnostic reasons for rejected or incomplete targets.

Callers must not independently reproduce label, style, or row-position rules.

## Required integrations

### Agent exposure

Present two separate collections to the agent:

- contextual sections and headings;
- writable targets.

Only writable target IDs may be passed to write tools. Labels remain useful for
LLM judgment, but the write call should use a stable node or target ID instead
of a free-form label.

This does not introduce deterministic label matching into the notes pipeline.
The LLM still chooses the semantically appropriate field. The backend validates
that the chosen destination is eligible.

### Write validation

All of these paths must call the same target registry:

- numeric extraction writer;
- notes writer;
- reviewer and source-write paths;
- scalar and HTML canonical fact APIs;
- manual edit/PATCH endpoints;
- imports and backfills.

A scratch-workbook mutation must not be reported as successful if its required
canonical projection is rejected.

### Canonical persistence

Canonical facts must reference reportable concepts and writable slots. Prose
notes should be attached to actual text-block concepts rather than only to
`(sheet, row, label)` identities.

Legacy invalid facts and notes cells should be marked `invalid_target` and
excluded from normal agent and mTool paths. They should remain visible through a
recovery workflow that permits move or delete. Do not silently delete them.

The backend owner of quarantine state should be the canonical persistence
module, with validation supplied by `FilingTargetRegistry`. The Notes Review UI
should display invalid prose cells in a dedicated recovery section with Move and
Delete actions. Numeric invalid facts should appear in Review values and in the
mTool preflight report. The mTool modal should report them but must not become
the only place where remediation is possible.

Adding taxonomy identity must be additive for existing identifiers. Numeric
gold is keyed by `concept_uuid`, while prose gold has its own `note_key`; these
identities and historical score rows must not be silently reminted. If an
identity change is unavoidable, introduce a versioned benchmark migration,
retain the old gold fingerprint and score history, and require explicit
re-baselining rather than comparing scores across identity versions.

### mTool export and reverse ingestion

mTool must independently validate facts rather than trust historical `kind`.
Export should expose one action, consistent with the recorded no-exposure-gate
decision, while reporting two output classifications:

- **Degraded compatibility output:** permits clearly reported legacy label fallback where
  operationally required.
- **Filing-ready output:** requires complete semantic identity and a verified template
  manifest; no label fallback is allowed.

Filing-grade preflight must block:

- abstract or structural concepts;
- dimensions, members, or hypercubes used as primary facts;
- presentation-only destinations;
- missing or ambiguous semantic addresses;
- manifest/workbook fingerprint mismatch;
- taxonomy-version mismatch;
- missing or ambiguous entity, period, unit, or dimensions;
- orphaned or quarantined canonical facts and notes.

Reverse ingestion and evaluation must apply the same concept-role checks. It
must count missing semantics for both ordinary leaves and matrix cells and must
not create benchmark mappings from invalid or label-only targets.

## Completeness guarantee

Field names should not be generated from the presentation linkbase alone.

The SSM schemas and linkbases establish concept identity, reportability,
hierarchy, labels, tables, and dimensions. The selected workbook variant
establishes the actual physical slots. A generated manifest must reconcile both
sources.

For every selected MFRS/MPERS, Company/Group, and statement variant, the
following must be true before filing is enabled:

1. Every writable mTool slot maps to exactly one reportable SSM primary item.
2. Every exported canonical fact maps to exactly one valid destination.
3. Every slot is classified, including presentation, formula, matrix, and
   metadata slots.
4. No abstract or structural concept is writable.
5. No duplicate, missing, or ambiguous mapping remains.
6. The workbook fingerprint matches the manifest version.

If any condition fails, the variant is `not filing-ready`. The system must not
silently omit fields or substitute label matching.

## Future canonical filing data

The current semantic address is not sufficient for a complete XBRL instance.
The canonical filing model will also need:

- namespace-qualified concept identity;
- source element ID for SSM/mTool adapter compatibility;
- entity identifier and scheme;
- actual instant or start/end dates;
- context dimensions and members;
- unit;
- decimals or precision;
- nil and language state;
- source and review provenance;
- taxonomy, manifest, and semantic-address versions.

`CY` and `PY` identify workbook column roles; they are not complete XBRL
contexts.

## Implementation sequence

### Phase 0: Pin the defect

- Commit the read-only audit command and its machine-readable result snapshot.
- Add exhaustive inventory and mismatch reports for every active template.
- Add negative regressions using the exact structural labels and coordinates
  shown in the external screenshots.
- Replace fixtures that use `Financial reporting status` as valid content with
  a genuine child field.
- Preserve explicit tests proving that the header is rejected.
- Audit deployed databases and receipts for populated invalid targets before
  choosing migration defaults.

### Phase 1: Establish authoritative metadata

- Add the taxonomy concept registry.
- Generate variant-specific template manifests from SSM schemas/linkbases and
  verified workbook layouts.
- Record source and workbook fingerprints.
- Fail manifest generation on missing, duplicate, or ambiguous mappings.
- Do not guess the unresolved MFRS numeric-note mappings by position.

### Phase 2: Enforce agent and writer boundaries

- Introduce `FilingTargetRegistry`.
- Separate contextual headings from writable agent targets.
- Require stable target IDs for writes.
- Apply the registry to every writer and projection path.
- Prevent successful scratch writes when canonical projection fails.

### Phase 3: Harden canonical persistence and review

- Require valid reportable-concept and template-slot references.
- Populate prose `notes_nodes` with real taxonomy identities.
- Quarantine existing invalid targets and provide move/delete recovery.
- Keep extraction available where reasonable while clearly reporting filing
  readiness separately.

### Phase 4: Add filing-readiness classification

- Require complete manifest-backed semantic addresses.
- Exclude label fallback from the filing-ready classification.
- Add independent export and reverse-ingestion validation.
- Expand the receipt with taxonomy version, manifest version, workbook
  fingerprint, mapping mode, and resolved concept/context information.

### Phase 5: Acceptance and rollout

- Audit existing databases and generate remediation reports.
- Run all-family round-trip tests.
- Perform Windows mTool 2.2 acceptance for every supported variant.
- Put a distinct sentinel value into a structural heading in a disposable
  workbook, run mTool Validate and Generate XBRL, and inspect both the validation
  result and generated instance. Record whether mTool itself rejects, ignores,
  or serializes the value.
- Mark output as filing-ready only for variants whose manifests pass all
  completeness gates. Keep the fill action exposed for degraded compatibility
  workflows under the recorded acknowledgement policy.

## Required automated gates

1. **All-template manifest contract**
   - Covers all 58 active workbooks and 74 worksheets.
   - Every slot is classified.
   - Every supported writable slot has exactly one valid mapping.
   - No writable slot resolves to an abstract, dimension, hypercube, or member.

2. **Agent exposure contract**
   - Agent writable-target catalogues exactly match the registry.
   - Headings may appear only as context.

3. **Writer rejection contract**
   - Every non-writable target is rejected across numeric, numeric-note, and
     prose-note write paths.

4. **Canonical persistence contract**
   - Invalid concept roles and presentation-only slots cannot become facts.
   - Prose cells require a valid writable notes target.
   - Legacy invalid content is quarantined and recoverable.

5. **mTool filing contract**
   - Filing-ready classification excludes incomplete semantics and label
     fallback.
   - Export and reverse ingestion preserve concept QName and dimensions.
   - Missing semantics for both leaf and matrix facts are blockers.

6. **Migration contract**
   - Every prior database schema upgrades idempotently.
   - Existing valid data is retained.
   - Invalid historical targets are reported without silent loss.

7. **External acceptance**
   - Each supported variant is opened and verified with Windows mTool 2.2.

## Independent reviewer checklist

The reviewer should verify these claims independently:

- Compare parser `kind` with XSD `abstract`, substitution group, and concept
  type across all active variants.
- Confirm the 26 dangerous numeric mismatches and examine the four reverse
  mismatches.
- Verify that agent catalogues exclude every non-writable slot after the fix.
- Exercise all writer and API paths, not only direct canonical writes.
- Confirm scratch and canonical stores cannot diverge on rejection.
- Verify the semantic mapping counts, especially the four MFRS numeric-note
  variants with zero coverage.
- Reject positional or label-only mapping as evidence of filing readiness.
- Confirm the manifest is variant-specific and fingerprint-pinned.
- Confirm prose text blocks resolve to real reportable taxonomy concepts.
- Confirm filing-grade preflight blocks every incomplete or invalid condition.
- Confirm compatibility behavior cannot be mistaken for filing readiness.
- Review migration and quarantine behavior for silent data loss.
- Re-run the committed audit command and compare its snapshot to the reviewed
  template fingerprints.

## Merge recommendation

The existing semantic-address work is useful groundwork, but the branch should
not be represented as resolving the header-as-field defect or as filing-ready.

Merge decisions should be separated:

1. Land immediate containment once the reproducible all-template guard tests
   pass and existing valid writable rows remain unchanged.
2. Treat the taxonomy/slot registry, canonical filing identity, and Windows
   acceptance as the separately approved filing-readiness program.

The semantic-address changes that were pending on the audited branch should not become the durable
canonical-to-mTool filing foundation until their taxonomy/slot assumptions and
incomplete variant mappings are corrected.
