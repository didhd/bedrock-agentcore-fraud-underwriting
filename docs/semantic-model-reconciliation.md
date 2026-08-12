# The customer's rules vs. ours

The customer delivered `FRAUDBOT_SEMANTIC_VIEW.yaml` on 2026-08-12. Its
`module_custom_instructions.sql_generation` field is the **authoritative** numbered rule list.
Until now `signal_layer/rules/` implemented a list *reconstructed* from the nine agent prompts,
so every disagreement would have meant a specialist reasoning off a rule the customer did not
write.

This is that comparison, run against the delivered file. No customer rule text appears below:
rules are referred to by number, and only our own implementation is quoted.

## Result: 25 of 25 implemented, and the numbers agree

| | |
|---|---|
| Rules in the delivered model | **25** (numbered 1–25; their "1.5" is a sub-point inside rule 1) |
| Implemented in `signal_layer/` | **25** |
| Loaded verbatim rather than retyped | **25** |
| Numeric or set disagreements found | **0** |

Reproduce with `MOCK_MODE=1 python3 -m pytest tests/test_semantic_reconciliation.py -q`.

### Rule-by-rule

| Rule | Subject | Where it lives |
|---|---|---|
| 1 (+1.5) | lender join, name cleaning, nickname table | `rules/r01_lender_join.py`, `rules/r0105_lender_nicknames.py` |
| 2 | borrower string normalisation | `rules/r02_normalize.py` |
| 3 | the ten alert categories | `alert_categories.py` |
| 4, 5 | alert-table join keys | `rules/r04_alert_join.py` |
| 6 | fraud-score bands | `bands.py` |
| 7 | charge-offs excluded from defaults | `rules/r07_chargeoff.py` |
| 8 | deprecated source tables | `rules/r08_deprecated_tables.py` |
| 9, 18 | co-borrower source, table choice | `rules/r09_coborrower_source.py` |
| 10 | active-dealer test | `rules/r10_active_dealer.py`, `bands.py` |
| 11 | submitted-at vs application date | `rules/r11_submitted_at.py` |
| 12 | never recommend automatic decline | `rules/r12_recommendations.py` |
| 13 | retro vs non-retro data | `rules/r13_retro.py` |
| 14 | distinct dealer identity | `rules/r10_active_dealer.py` |
| 15 | null SSNs are not distinct | `rules/r15_null_ssn.py` |
| 16, 19 | lender-name aliasing | `rules/r0105_lender_nicknames.py` |
| 17 | fake emails, common VINs and phones | `rules/r17_fake_records.py` |
| 20, 22 | IncomePass score, score averaging | `bands.py` |
| 21 | the two dealer scores | `bands.py` |
| 23 | never return raw SSN | `rules/catalog.py` |
| 24 | PRODUCT field decode | `bands.py` |
| 25 | invalid credit-report values | `bands.py` |

Rules 16 and 19 are worth a note on method: a first pass searched our source for each rule's
text *inline* and reported them missing. They are not missing — `r0105_lender_nicknames.py`
loads them through `rule_text()` at import, which is the correct pattern and is why an inline
search does not find them. The search was wrong, not the code.

## The four numeric checks, in detail

These are the ones where a mismatch would change adjudications rather than wording.

**Alert categories (rule 3) — exact match.** Ten categories, and the ID sets are identical
member for member, not merely equal in size:

| Category | IDs |
|---|---|
| Identity | 60 |
| Default | 33 |
| Employment | 21 |
| Income | 12 |
| Employment Deviation | 10 |
| Straw Borrower | 10 |
| Dealer Risk | 9 |
| Collateral | 6 |
| Vehicle Risk | 3 |
| Bust Out Risk | 2 |

**148 distinct IDs**, 166 category memberships — so 18 IDs belong to more than one category.

> **CLAUDE.md correction.** Its architecture section says "the ten alert-ID categories (166
> IDs)". 166 is the membership count, not the ID count. The distinct-ID count is **148**, which
> is what the other CLAUDE.md passage already said. Fixed there.

**Fraud-score bands (rule 6) — exact match**, including the special value:

```python
FRAUD_SCORE_BANDS = {"low": (0, 299), "medium": (300, 699), "high": (700, 998),
                     "very high": (999, 999)}
```

The 999 case is not a band boundary but a signal in its own right — it means an accelerator
fired — and `FRAUD_SCORE_999_MEANING` carries the customer's own sentence on that.

**The two dealer scores (rule 21) — not conflated.** Rule 21 defines two distinct dealer-risk
scores: one scoped to a single lender's portfolio, one aggregated across the consortium. They key
on different identifiers, and `bands.DEALER_SCORE_BINDING` records that binding — a two-entry
mapping from each score to the identifier tuple it is keyed by, matching the rule exactly.

Column names are deliberately not reproduced here; read `signal_layer/bands.py`. A single
dealer-score concept keyed on one identifier would silently mix per-lender risk with
consortium-wide risk. It does not.

**PRODUCT decode (rule 24) and credit validity (rule 25) — match.** `bands.PRODUCTS` carries
all seven acronyms with the customer's own product descriptions, and `is_valid_time_in_file`
implements rule 25's two conditions.

## Two things to raise with the customer

**1. Rule 12 conflicts with their own adjudication contract.**

Rule 12 forbids recommending an automatic decline; the recommendation should be for further
analysis. But `master_agent_prompt.txt` defines `recommendation_decision` with three values, one
of which is `DECLINE`, and `agents/contracts.py` implements that contract exactly.

Both are the customer's own artefacts, so this is theirs to resolve, and we have not changed
either. `rules/r12_recommendations.py` enforces the *narrative* rule — a specialist must not
tell a lender to decline — while the structured field keeps the enum their master prompt
specifies. Worth asking whether `DECLINE` in the structured output is intended to mean
"decline" or "do not fund without review".

This sits alongside the enum conflict already open in CLAUDE.md: the prompt file says mid-band
`MEDIUM RISK` with three recommendation values, and the process-guide PDF says `POSSIBLE RISK`
with four.

**2. One verified query reads a table the semantic model does not declare.**

The co-borrower and score-bin queries reference a `performance` table that is absent from the
model's 24 declared tables. So the model is not a complete description of what their own
verified queries touch.

This has a concrete consequence, already handled: the read allowlist the Gateway Lambda applies
to a verified query is derived **from the query**, not from the model
(`UseCase.referenced_tables`). Deriving it from the model would have rejected two of the
seventeen queries. Asserted by `tests/test_usecase_tools.py::test_referenced_tables_come_from_the_query_not_the_model`,
so if a later delivery adds the table, the test says so.

## Two confidentiality findings in existing code

`signal_layer/rules/r0105_lender_nicknames.py` is git-tracked and contains the customer's
lender **nickname table as literal values** — twelve canonical lender names and their aliases.
Rule text itself is loaded through `rule_text()` and is therefore safe; the nickname mapping is
not, it is inline.

Lender names are customer data in the same sense their column names are, and
`prompts/verbatim/` is gitignored precisely to keep that class of material out of the
repository. This predates the semantic-model work and is **not** something to fix in a hurry:
the table is a `Mapping` that several rules resolve against, and moving it behind the loader
changes how those rules initialise. Flagged for a considered change, with the safe pattern
already in the same file — `RULE_TEXT`, `RULE_16_TEXT` and `RULE_19_TEXT` all load rather than
inline.

**Second: customer column names appear in committed code and prose.** `signal_layer/bands.py`
carries identifier names inside `DEALER_SCORE_BINDING`, and `README.md`'s fidelity table names a
join column. Column names are a weaker exposure than SQL or lender names, and the repo has always
treated the 21-key contract's own field names as publishable — they are the interface, not the
schema. But the line between "our contract's field" and "their column" is not drawn anywhere,
and it should be, before this repo is shown more widely.

Neither finding is safe to fix in a hurry: `DEALER_SCORE_BINDING` is resolved against by several
rules, and moving it behind the loader changes how they initialise.

`tools/usecases/` was built to this standard from the start: its selectors were rewritten to
avoid the lender names that appear in the customer's own question text, and
`tests/test_usecase_tools.py::test_no_pattern_contains_a_customer_literal` plus
`test_no_customer_sql_is_committed` enforce it.

## What this settles

- CLAUDE.md open question 3 asked for `full_underwriting_signal_dictionary.xlsx` to settle the
  signal set and the rules. This YAML settles the **rules** — all 25, authoritatively. It does
  not settle the ~75-signal registry, which is still reconstructed from the nine prompts.
- CLAUDE.md open question 2 asked whether their Cortex Analyst is backed by a semantic VIEW or
  a stage-based YAML. It is the **YAML**. Snowflake's managed Cortex Analyst MCP server supports
  views only, so keeping their semantic layer by pointing at that server is not available.
- The reconstruction was correct. That is the useful headline: the rules encoded from the agent
  prompts match the authoritative list with no numeric or set disagreement.
