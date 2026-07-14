# Safe Substring Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit, backward-compatible substring match mode that turns requests such as `HZ → CS` into verified full-label edits such as `HZ25-4DPP → CS25-4DPP`.

**Architecture:** Keep OCR candidates as complete labels. Extend the request contract with `match_mode` and a signed one-based `substring_occurrence`, derive a complete target label before rendering, and bind that derivation into candidate reports and HMAC tokens. Reuse the existing whole-polygon repair, atomic publication, pixel-boundary checks, and post-OCR geometry validation.

**Tech Stack:** Python 3.12, dataclasses, Pillow, OpenCV, PaddleOCR, pytest, JSON/HMAC candidate reports, PowerShell on Windows.

---

## File Map

- Modify `skills/edit-chart-text/src/edit_chart_text/models.py`: declare match-mode and occurrence fields.
- Modify `skills/edit-chart-text/src/edit_chart_text/request_io.py`: strictly parse and validate the new request fields.
- Modify `skills/edit-chart-text/src/edit_chart_text/matching.py`: perform literal substring matching and derive full target labels.
- Modify `skills/edit-chart-text/src/edit_chart_text/pipeline.py`: expand occurrence candidates, sign derived labels, render full labels, and validate full labels after OCR.
- Modify `skills/edit-chart-text/tests/test_models.py`: lock the public dataclass type contract.
- Modify `skills/edit-chart-text/tests/test_request_io.py`: cover defaults and invalid combinations.
- Modify `skills/edit-chart-text/tests/test_matching.py`: cover substring selection and full-label derivation.
- Modify `skills/edit-chart-text/tests/test_pipeline.py`: cover all-candidate edits and multiple-occurrence confirmation.
- Modify `skills/edit-chart-text/tests/test_hardening.py`: cover token tampering and failed post-OCR publication.
- Modify `skills/edit-chart-text/SKILL.md`: tell future Codex instances when and how to use substring mode.
- Optionally modify `skills/edit-chart-text/agents/openai.yaml` only if the existing trigger description fails forward validation.

### Task 1: Extend and validate the request contract

**Files:**
- Modify: `skills/edit-chart-text/src/edit_chart_text/models.py`
- Modify: `skills/edit-chart-text/src/edit_chart_text/request_io.py`
- Test: `skills/edit-chart-text/tests/test_models.py`
- Test: `skills/edit-chart-text/tests/test_request_io.py`

- [ ] **Step 1: Write failing model and request-parser tests**

Add these assertions to `test_models.py`:

```python
assert replacement_hints["match_mode"] == Literal["exact", "substring"]
assert replacement_hints["substring_occurrence"] == int | None
```

Import `Literal` from `typing`. Add the following tests to `test_request_io.py`, using its existing `write_request` helper:

```python
def test_match_mode_defaults_to_exact_and_parses_substring(tmp_path):
    image = tmp_path / "chart.png"
    image.write_bytes(b"image")
    default = load_request(write_request(tmp_path, {
        "image_path": "chart.png",
        "replacements": [{"old_text": "HZ", "new_text": "CS"}],
    }))
    explicit = load_request(write_request(tmp_path, {
        "image_path": "chart.png",
        "replacements": [{"old_text": "HZ", "new_text": "CS", "match_mode": "substring"}],
    }))
    assert default.replacements[0].match_mode == "exact"
    assert default.replacements[0].substring_occurrence is None
    assert explicit.replacements[0].match_mode == "substring"


@pytest.mark.parametrize("replacement, message", [
    ({"old_text": "HZ", "new_text": "CS", "match_mode": "contains"}, "match_mode"),
    ({"old_text": "HZ", "new_text": "HZ", "match_mode": "substring"}, "must differ"),
    ({"old_text": "HZ", "new_text": "CS", "substring_occurrence": 1}, "match_mode=substring"),
    ({"old_text": "HZ", "new_text": "CS", "match_mode": "substring", "substring_occurrence": 0}, "positive"),
    ({"old_text": "HZ", "new_text": "CS", "match_mode": "substring", "substring_occurrence": True}, "positive"),
    ({"old_text": "HZ", "new_text": "CS", "match_mode": "substring", "substring_occurrence": 1}, "candidate selection"),
])
def test_rejects_invalid_substring_contract(tmp_path, replacement, message):
    payload = {"image_path": "chart.png", "replacements": [replacement]}
    with pytest.raises(ValueError, match=message):
        load_request(write_request(tmp_path, payload))
```

Add a valid confirmed selection case:

```python
def test_parses_confirmed_substring_occurrence(tmp_path):
    report = tmp_path / "report.json"
    report.write_text("{}", encoding="utf-8")
    request = load_request(write_request(tmp_path, {
        "image_path": "chart.png",
        "confirmation_report_path": "report.json",
        "replacements": [{
            "old_text": "HZ", "new_text": "CS", "scope": "one",
            "match_mode": "substring", "substring_occurrence": 2,
            "candidate_number": 7,
            "candidate_polygon": [[1,1],[20,1],[20,10],[1,10]],
            "candidate_token": "secure-token-value",
        }],
    }))
    replacement = request.replacements[0]
    assert replacement.match_mode == "substring"
    assert replacement.substring_occurrence == 2
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest skills/edit-chart-text/tests/test_models.py skills/edit-chart-text/tests/test_request_io.py -p no:cacheprovider --basetemp C:\tmp\substring-contract-red -q
```

Expected: failures because `Replacement` lacks both fields and `_load_replacement` accepts unknown/illegal substring combinations.

- [ ] **Step 3: Add the minimal model fields**

In `models.py`, import and declare the mode type, then append fields so existing positional constructors remain compatible:

```python
MatchMode = Literal["exact", "substring"]

@dataclass(frozen=True)
class Replacement:
    old_text: str
    new_text: str
    scope: Scope = "ask"
    location_hint: str | None = None
    candidate_number: int | None = None
    candidate_polygon: Polygon | None = None
    candidate_token: str | None = None
    match_mode: MatchMode = "exact"
    substring_occurrence: int | None = None
```

- [ ] **Step 4: Implement strict parsing**

In `request_io.py`, add `_VALID_MATCH_MODES = {"exact", "substring"}`. In `_load_replacement`, after scope validation and before candidate-field validation, parse:

```python
match_mode = item.get("match_mode", "exact")
if not isinstance(match_mode, str) or match_mode not in _VALID_MATCH_MODES:
    raise ValueError(f"{context}.match_mode must be one of: exact, substring")
old_text, new_text = old_text.strip(), new_text.strip()
if match_mode == "substring" and old_text == new_text:
    raise ValueError(f"{context}.old_text and new_text must differ in substring mode")

occurrence_present = "substring_occurrence" in item
occurrence = item.get("substring_occurrence")
if occurrence_present and (type(occurrence) is not int or occurrence <= 0):
    raise ValueError(f"{context}.substring_occurrence must be a positive integer")
if occurrence_present and match_mode != "substring":
    raise ValueError(f"{context}.substring_occurrence requires match_mode=substring")
```

After `number_present` is known, add:

```python
if occurrence_present and not number_present:
    raise ValueError(f"{context}.substring_occurrence requires candidate selection fields")
```

Return `Replacement` with keywords to prevent field-order mistakes:

```python
return Replacement(
    old_text=old_text,
    new_text=new_text,
    scope=scope,
    location_hint=hint,
    candidate_number=number,
    candidate_polygon=polygon,
    candidate_token=token,
    match_mode=match_mode,
    substring_occurrence=occurrence,
)
```

- [ ] **Step 5: Run focused and full request tests**

Run the command from Step 2. Expected: all selected tests pass. Then run:

```powershell
python -m pytest skills/edit-chart-text/tests/test_cli.py skills/edit-chart-text/tests/test_request_io.py -p no:cacheprovider --basetemp C:\tmp\substring-contract-green -q
```

Expected: PASS with no regressions in CLI request loading.

- [ ] **Step 6: Commit the contract change**

```powershell
git add skills/edit-chart-text/src/edit_chart_text/models.py skills/edit-chart-text/src/edit_chart_text/request_io.py skills/edit-chart-text/tests/test_models.py skills/edit-chart-text/tests/test_request_io.py
git commit -m "feat: add explicit substring request mode"
```

### Task 2: Add literal substring matching and full-label derivation

**Files:**
- Modify: `skills/edit-chart-text/src/edit_chart_text/matching.py`
- Test: `skills/edit-chart-text/tests/test_matching.py`

- [ ] **Step 1: Write failing matching tests**

Import `derive_target_label` and `substring_occurrences`, then add:

```python
def test_substring_all_selects_every_unique_literal_candidate():
    choices = (
        candidate("HZ21-1A/B平台", x=0),
        candidate("other", x=20),
        candidate("HZ25-4DPP", x=40),
    )
    replacement = Replacement("HZ", "CS", "all", match_mode="substring")
    result = choose_candidates(replacement, choices)
    assert result.status == "ready"
    assert result.candidates == (choices[0], choices[2])
    assert derive_target_label(replacement, choices[0]) == ("HZ21-1A/B平台", "CS21-1A/B平台", 1)


def test_substring_mode_is_case_sensitive_and_has_no_fuzzy_fallback():
    replacement = Replacement("HZ", "CS", "all", match_mode="substring")
    result = choose_candidates(replacement, (candidate("hz25"), candidate("H2-25")))
    assert result.status == "not_found"
    assert result.candidates == ()


def test_multiple_occurrences_require_confirmation_and_can_be_derived_by_index():
    choice = candidate("HZ-HZ")
    replacement = Replacement("HZ", "CS", "one", match_mode="substring")
    result = choose_candidates(replacement, (choice,))
    assert result.status == "needs_confirmation"
    assert substring_occurrences("HZ-HZ", "HZ") == (0, 3)
    assert derive_target_label(replacement, choice, 1) == ("HZ-HZ", "CS-HZ", 1)
    assert derive_target_label(replacement, choice, 2) == ("HZ-HZ", "HZ-CS", 2)
    with pytest.raises(ValueError, match="occurrence"):
        derive_target_label(replacement, choice, 3)
```

- [ ] **Step 2: Run the focused test and verify RED**

```powershell
python -m pytest skills/edit-chart-text/tests/test_matching.py -p no:cacheprovider --basetemp C:\tmp\substring-matching-red -q
```

Expected: import failures for the new helpers and incorrect `not_found` behavior for literal substrings.

- [ ] **Step 3: Implement occurrence discovery and target derivation**

Add to `matching.py`:

```python
def substring_occurrences(text: str, needle: str) -> tuple[int, ...]:
    positions: list[int] = []
    start = 0
    while True:
        position = text.find(needle, start)
        if position < 0:
            return tuple(positions)
        positions.append(position)
        start = position + len(needle)


def derive_target_label(
    replacement: Replacement,
    candidate: TextCandidate,
    occurrence: int | None = None,
) -> tuple[str, str, int | None]:
    source_label = candidate.text
    if replacement.match_mode == "exact":
        return source_label, replacement.new_text, None
    positions = substring_occurrences(source_label, replacement.old_text)
    chosen = occurrence or replacement.substring_occurrence
    if chosen is None:
        if len(positions) != 1:
            raise ValueError("substring occurrence must be selected when candidate contains multiple matches")
        chosen = 1
    if chosen > len(positions):
        raise ValueError("substring occurrence is outside the current OCR candidate")
    position = positions[chosen - 1]
    target_label = (
        source_label[:position]
        + replacement.new_text
        + source_label[position + len(replacement.old_text):]
    )
    return source_label, target_label, chosen
```

- [ ] **Step 4: Add the explicit substring branch to `choose_candidates`**

Place this branch before existing exact/fuzzy logic:

```python
if replacement.match_mode == "substring":
    literal = tuple(
        item for item in candidates
        if item.confidence >= 0.80
        and substring_occurrences(item.text, replacement.old_text)
    )
    if not literal:
        return MatchDecision("not_found")
    if any(len(substring_occurrences(item.text, replacement.old_text)) != 1 for item in literal):
        return MatchDecision("needs_confirmation", literal)
    if replacement.scope == "all" or len(literal) == 1:
        return MatchDecision("ready", literal)
    return MatchDecision("needs_confirmation", literal)
```

Do not alter the existing exact/fuzzy branch.

- [ ] **Step 5: Run matching tests and the exact-mode regression**

Run the command from Step 2. Expected: PASS. Confirm the existing low-confidence/fuzzy tests still pass unchanged.

- [ ] **Step 6: Commit matching behavior**

```powershell
git add skills/edit-chart-text/src/edit_chart_text/matching.py skills/edit-chart-text/tests/test_matching.py
git commit -m "feat: derive full labels from substring matches"
```

### Task 3: Bind substring occurrences into confirmation reports and tokens

**Files:**
- Modify: `skills/edit-chart-text/src/edit_chart_text/pipeline.py`
- Test: `skills/edit-chart-text/tests/test_pipeline.py`
- Test: `skills/edit-chart-text/tests/test_hardening.py`

- [ ] **Step 1: Write a failing multi-occurrence confirmation test**

Add to `test_pipeline.py`:

```python
def test_substring_multiple_occurrences_emit_signed_occurrence_choices(tmp_path):
    source = chart(tmp_path)
    repeated = TextCandidate("HZ-HZ", ((10,10),(60,10),(60,24),(10,24)), .99)
    request = EditRequest(source, (
        Replacement("HZ", "CS", "one", match_mode="substring"),
    ))
    first = run_pipeline(request, SequenceOCR((repeated,)))
    assert first.status == "needs_confirmation"
    assert [record["substring_occurrence"] for record in first.edits] == [1, 2]
    assert [record["source_label"] for record in first.edits] == ["HZ-HZ", "HZ-HZ"]
    assert [record["target_label"] for record in first.edits] == ["CS-HZ", "HZ-CS"]
    assert len({record["candidate_token"] for record in first.edits}) == 2
```

Add a confirmation helper that copies every bound field:

```python
def confirm_substring_occurrence(source, report, record):
    return EditRequest(source, (Replacement(
        "HZ", "CS", "one",
        candidate_number=record["candidate_number"],
        candidate_polygon=tuple(map(tuple, record["polygon"])),
        candidate_token=record["candidate_token"],
        match_mode="substring",
        substring_occurrence=record["substring_occurrence"],
    ),), Path(report.report_path))


def test_confirmed_substring_occurrence_edits_only_selected_position(tmp_path):
    source = chart(tmp_path)
    repeated = TextCandidate("HZ-HZ", ((10,10),(60,10),(60,24),(10,24)), .99)
    first = run_pipeline(EditRequest(source, (
        Replacement("HZ", "CS", "one", match_mode="substring"),
    )), SequenceOCR((repeated,)))
    request = confirm_substring_occurrence(source, first, first.edits[1])
    second = run_pipeline(request, SequenceOCR((repeated,), (
        TextCandidate("HZ-CS", repeated.polygon, .99),
    )))
    assert second.status == "success", second.messages
    assert second.edits[0]["target_label"] == "HZ-CS"
    assert second.edits[0]["substring_occurrence"] == 2
```

- [ ] **Step 2: Write a failing token-tamper test**

Add to `test_hardening.py`:

```python
def test_substring_token_binds_occurrence_and_derived_labels(tmp_path):
    source = chart(tmp_path)
    repeated = TextCandidate("HZ-HZ", ((10,10),(60,10),(60,24),(10,24)), .99)
    first = run_pipeline(EditRequest(source, (
        Replacement("HZ", "CS", "one", match_mode="substring"),
    )), SequenceOCR((repeated,)))
    record = first.edits[0]
    tampered = EditRequest(source, (Replacement(
        "HZ", "CS", "one",
        candidate_number=record["candidate_number"],
        candidate_polygon=tuple(map(tuple, record["polygon"])),
        candidate_token=record["candidate_token"],
        match_mode="substring",
        substring_occurrence=2,
    ),), Path(first.report_path))
    result = run_pipeline(tampered, SequenceOCR((repeated,)))
    assert result.status == "needs_confirmation"
    assert result.output_path is None
    assert "binding" in " ".join(result.messages).lower()
```

- [ ] **Step 3: Run both tests and verify RED**

```powershell
python -m pytest skills/edit-chart-text/tests/test_pipeline.py::test_substring_multiple_occurrences_emit_signed_occurrence_choices skills/edit-chart-text/tests/test_hardening.py::test_substring_token_binds_occurrence_and_derived_labels -p no:cacheprovider --basetemp C:\tmp\substring-token-red -q
```

Expected: confirmation records lack occurrence/derived-label fields and current tokens do not bind them.

- [ ] **Step 4: Expand `CandidateEntry` and candidate records**

Import `derive_target_label` and `substring_occurrences` from `matching.py`. Change the alias to:

```python
CandidateEntry = tuple[int, int, Replacement, TextCandidate, int | None]
```

Replace `_numbered_entries` with logic that emits one entry per occurrence:

```python
def _numbered_entries(decisions, numbers, *, include_ready: bool) -> list[CandidateEntry]:
    entries: list[CandidateEntry] = []
    for index, (replacement, decision) in enumerate(decisions):
        if not include_ready and decision.status == "ready":
            continue
        for candidate in decision.candidates:
            if replacement.match_mode == "substring":
                occurrences = tuple(range(
                    1,
                    len(substring_occurrences(candidate.text, replacement.old_text)) + 1,
                ))
            else:
                occurrences = (None,)
            for occurrence in occurrences:
                entries.append((numbers[id(candidate)], index, replacement, candidate, occurrence))
    return entries
```

Update `_stage_preview` to unpack five fields. Its polygon label remains the stable OCR candidate number; occurrence choices are distinguished in the JSON report.

- [ ] **Step 5: Sign and report all substring identity fields**

Extend `_candidate_payload` parameters and dictionary with `match_mode`, `source_label`, `target_label`, and `substring_occurrence`. In `_candidate_record`, accept `occurrence`, call:

```python
source_label, target_label, resolved_occurrence = derive_target_label(
    replacement, candidate, occurrence
)
```

Include these values in both the signed payload and returned report record. Preserve `text` as the raw OCR text for compatibility.

- [ ] **Step 6: Verify selections against the expanded payload**

In `_selection_error`, rebuild the token payload with the four new fields read from the record. Extend `expected` with:

```python
and record.get("match_mode") == replacement.match_mode
and record.get("substring_occurrence") == replacement.substring_occurrence
```

For substring mode, also require both report labels to be non-empty strings. In `_resolve_selected`, after geometry selects exactly one candidate, call `derive_target_label(replacement, matches[0])`; convert `ValueError` into a `needs_confirmation` decision and a message ending in `reconfirm`.

- [ ] **Step 7: Run confirmation and hardening regressions**

```powershell
python -m pytest skills/edit-chart-text/tests/test_pipeline.py skills/edit-chart-text/tests/test_hardening.py -p no:cacheprovider --basetemp C:\tmp\substring-token-green -q
```

Expected: all tests pass, including existing report-path, source-digest, polygon, run-ID, and token tamper tests.

- [ ] **Step 8: Commit confirmation security changes**

```powershell
git add skills/edit-chart-text/src/edit_chart_text/pipeline.py skills/edit-chart-text/tests/test_pipeline.py skills/edit-chart-text/tests/test_hardening.py
git commit -m "feat: bind substring occurrences to candidate tokens"
```

### Task 4: Render and post-validate complete derived labels

**Files:**
- Modify: `skills/edit-chart-text/src/edit_chart_text/pipeline.py`
- Test: `skills/edit-chart-text/tests/test_pipeline.py`
- Test: `skills/edit-chart-text/tests/test_hardening.py`
- Test: `skills/edit-chart-text/tests/test_final_quality.py`

- [ ] **Step 1: Write a failing all-candidate pipeline test**

Add to `test_pipeline.py`:

```python
def test_substring_all_renders_and_validates_complete_labels(tmp_path):
    source = chart(tmp_path, second=True)
    first = (candidate("HZ25-4DPP", x=5), candidate("HZ25-8DPP", x=40))
    post = (candidate("CS25-4DPP", x=5), candidate("CS25-8DPP", x=40))
    request = EditRequest(source, (
        Replacement("HZ", "CS", "all", match_mode="substring"),
    ))
    result = run_pipeline(request, SequenceOCR(first, post))
    assert result.status == "success", result.messages
    assert [(edit["source_label"], edit["target_label"]) for edit in result.edits] == [
        ("HZ25-4DPP", "CS25-4DPP"),
        ("HZ25-8DPP", "CS25-8DPP"),
    ]
    assert all(edit["post_ocr_validation"]["passed"] for edit in result.edits)
```

Use non-overlapping polygons wide enough for the labels so the test exercises substring behavior rather than conflict detection.

- [ ] **Step 2: Write a failing rollback test for derived labels**

Add to `test_hardening.py`:

```python
def test_substring_post_ocr_failure_does_not_publish(tmp_path):
    source = chart(tmp_path)
    detected = (TextCandidate("HZ26-6DPP（待建）", ((5,8),(75,8),(75,26),(5,26)), .99),)
    request = EditRequest(source, (
        Replacement("HZ", "CS", "all", match_mode="substring"),
    ))
    result = run_pipeline(request, SequenceOCR(detected, ()))
    assert result.status == "failed"
    assert result.output_path is None
    assert result.edits[0]["source_label"] == "HZ26-6DPP（待建）"
    assert result.edits[0]["target_label"] == "CS26-6DPP（待建）"
    assert not tuple(tmp_path.glob("chart_*_edited.png"))
```

- [ ] **Step 3: Run new tests and verify RED**

```powershell
python -m pytest skills/edit-chart-text/tests/test_pipeline.py::test_substring_all_renders_and_validates_complete_labels skills/edit-chart-text/tests/test_hardening.py::test_substring_post_ocr_failure_does_not_publish -p no:cacheprovider --basetemp C:\tmp\substring-render-red -q
```

Expected: rendering still uses `replacement.new_text`, and post-OCR still compares requested fragments rather than complete labels.

- [ ] **Step 4: Render derived labels and record audit fields**

In `_ready`, replace direct rendering with:

```python
source_label, target_label, occurrence = derive_target_label(replacement, candidate)
working = render_replacement(working, candidate, target_label, style, allowed)
```

Build each edit record with:

```python
{
    "old_text": replacement.old_text,
    "new_text": replacement.new_text,
    "match_mode": replacement.match_mode,
    "source_label": source_label,
    "target_label": target_label,
    "substring_occurrence": occurrence,
    "confidence": candidate.confidence,
    "polygon": [list(point) for point in candidate.polygon],
    "allowed_box": list(allowed),
    "repair_method": method,
    "style": asdict(style),
}
```

- [ ] **Step 5: Validate complete labels after OCR**

In `_post_validate`, select matches using:

```python
new = [candidate for candidate in region if candidate.text.strip() == edit["target_label"]]
old = [candidate for candidate in region if candidate.text.strip() == edit["source_label"]]
```

Keep geometry, confidence, count, and atomic-failure rules unchanged. Preserve the existing message prefixes `post-OCR new_text validation failed` and `post-OCR old_text validation failed` for compatibility, but interpolate `target_label` and `source_label` as their expected values. Add `source_label` and `target_label` to each `post_ocr_validation` dictionary so reports are self-describing.

- [ ] **Step 6: Run pipeline, hardening, and quality tests**

```powershell
python -m pytest skills/edit-chart-text/tests/test_pipeline.py skills/edit-chart-text/tests/test_hardening.py skills/edit-chart-text/tests/test_final_quality.py -p no:cacheprovider --basetemp C:\tmp\substring-render-green -q
```

Expected: PASS. Existing exact-mode edit records should report `source_label` equal to the OCR candidate and `target_label` equal to `new_text` without changing pixel behavior.

- [ ] **Step 7: Commit rendering and validation**

```powershell
git add skills/edit-chart-text/src/edit_chart_text/pipeline.py skills/edit-chart-text/tests/test_pipeline.py skills/edit-chart-text/tests/test_hardening.py skills/edit-chart-text/tests/test_final_quality.py
git commit -m "feat: validate complete labels after substring edits"
```

### Task 5: Update the Skill workflow and validate metadata

**Files:**
- Modify: `skills/edit-chart-text/SKILL.md`
- Verify: `skills/edit-chart-text/agents/openai.yaml`
- Test: `skills/edit-chart-text/tests/test_package.py`

- [ ] **Step 1: Add a failing package assertion for substring guidance**

In `test_package.py`, add:

```python
def test_skill_documents_safe_substring_confirmation():
    skill = (Path(__file__).parents[1] / "SKILL.md").read_text(encoding="utf-8")
    assert "match_mode" in skill
    assert "substring" in skill
    assert "substring_occurrence" in skill
```

- [ ] **Step 2: Run the package test and verify RED**

```powershell
python -m pytest skills/edit-chart-text/tests/test_package.py -p no:cacheprovider --basetemp C:\tmp\substring-skill-red -q
```

Expected: failure because the current workflow does not document substring mode.

- [ ] **Step 3: Update `SKILL.md` concisely**

Amend request extraction to include `match_mode`. Add these imperative rules after scope selection:

```markdown
- 当用户要求修改完整 OCR 标签内部的字面片段时设置 `match_mode=substring`；否则省略该字段并保持默认 `exact`。子串匹配区分大小写，不使用正则或模糊匹配。
- 子串候选报告中的 `source_label`、`target_label` 和 `substring_occurrence` 属于确认绑定。二次请求必须从同一候选记录原样复制 `substring_occurrence`；同一标签出现多次旧文字时不得自行猜测 occurrence。
```

Extend the confirmation field list to include `substring_occurrence` when present. Keep the file under 500 lines.

- [ ] **Step 4: Validate the Skill and UI metadata**

```powershell
$env:PYTHONUTF8='1'
python C:\Users\飞翔无限\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills/edit-chart-text
python -m pytest skills/edit-chart-text/tests/test_package.py -p no:cacheprovider --basetemp C:\tmp\substring-skill-green -q
```

Expected: `Skill is valid!` and package tests pass. Inspect `agents/openai.yaml`; its existing generic description already covers substring requests, so do not regenerate it unless the package test demonstrates stale metadata.

- [ ] **Step 5: Commit the workflow documentation**

```powershell
git add skills/edit-chart-text/SKILL.md skills/edit-chart-text/tests/test_package.py
git commit -m "docs: teach skill safe substring replacement"
```

### Task 6: Full regression, real-image acceptance, installation sync

**Files:**
- Create beside the user image: `010-substring-request-20260714.json`
- Verify: `I:/学习/AI/EIA/data/EIA/project/figs/03-现场调查_CS15-8油田综合调整可行性研究项目春季环境质量现状调查与评价/010.jpg`
- Sync runtime to: `C:/Users/飞翔无限/.codex/skills/edit-chart-text`

- [ ] **Step 1: Run the complete offline suite**

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest skills/edit-chart-text/tests -p no:cacheprovider --basetemp C:\tmp\substring-full-suite
```

Expected: all tests pass, with only the existing intentional integration deselection/skip.

- [ ] **Step 2: Create the real substring request without overwriting the source**

Write UTF-8 JSON beside `010.jpg`:

```json
{
  "image_path": "010.jpg",
  "replacements": [
    {"old_text": "HZ", "new_text": "CS", "scope": "all", "match_mode": "substring"},
    {"old_text": "HYSY115", "new_text": "HYSY", "scope": "one", "match_mode": "substring"},
    {"old_text": "南海奋进", "new_text": "NHXW", "scope": "one", "match_mode": "substring"}
  ]
}
```

- [ ] **Step 3: Run real OCR with the validated ASCII cache**

Use only process-local environment variables:

```powershell
$cache='C:\tmp\edit-chart-text-ocr-cache'
$env:PYTHONUTF8='1'
$env:PADDLE_PDX_CACHE_HOME=Join-Path $cache 'paddlex'
$env:PADDLE_HOME=Join-Path $cache 'paddle'
$env:XDG_CACHE_HOME=Join-Path $cache 'xdg'
$env:PADDLE_EXTENSION_DIR=Join-Path $cache 'paddle-extension'
$env:USERPROFILE=Join-Path $cache 'profile'
$env:FLAGS_use_mkldnn='0'
$env:PYTHONPATH='C:\Users\飞翔无限\Documents\图片改字\skills\edit-chart-text\src'
python -m edit_chart_text.cli --request 'I:\学习\AI\EIA\data\EIA\project\figs\03-现场调查_CS15-8油田综合调整可行性研究项目春季环境质量现状调查与评价\010-substring-request-20260714.json'
```

Expected: exit code 0 with unique `output:` and `report:` paths.

- [ ] **Step 4: Audit the real report and source hash**

Verify the report has six edits whose pairs are:

```text
HZ21-1A/B平台 -> CS21-1A/B平台
HZ25-4DPP -> CS25-4DPP
HZ25-8DPP -> CS25-8DPP
HZ26-6DPP（待建） -> CS26-6DPP（待建）
HYSY115FPSO -> HYSYFPSO
南海奋进FPSO -> NHXWFPSO
```

For every edit assert `quality_checks` are all true and `post_ocr_validation.passed` is true. Recompute SHA256 for `010.jpg` and compare it with `source_sha256` in the report.

- [ ] **Step 5: Commit the finished implementation**

Do not add the user image, request JSON, generated images, reports, caches, or model files. Run `git status --short`, then commit only tracked source/test/doc changes if any remain:

```powershell
git add skills/edit-chart-text
git commit -m "feat: support safe substring text replacement"
```

If prior task commits already contain every tracked change, record the current HEAD instead of creating an empty commit.

- [ ] **Step 6: Synchronize only verified runtime files**

Replace the installed `agents`, `references`, and `src` directories and copy `SKILL.md` plus `pyproject.toml` from the repository. Exclude `tests`, `.pytest_cache`, `__pycache__`, generated images, reports, and requests. Refresh the editable entry point without changing dependencies:

```powershell
python -m pip install --no-deps -e C:\Users\飞翔无限\.codex\skills\edit-chart-text
edit-chart-text --help
```

- [ ] **Step 7: Verify installed-source identity**

Hash `SKILL.md`, `pyproject.toml`, and every file under `agents`, `references`, and `src` in both repository and installed directories, excluding `__pycache__`. Expected: zero mismatches and zero forbidden artifacts in the installed copy.

- [ ] **Step 8: Final status check**

```powershell
git status --short
git log -5 --oneline
```

Expected: clean working tree. Report the final commit, offline test count, real-image output/report paths, installed CLI verification, and any non-project ACL cache residue separately.
