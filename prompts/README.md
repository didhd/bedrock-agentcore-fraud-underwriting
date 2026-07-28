# prompts/

This package loads the customer's nine production agent prompts and their nine
orchestration documents **verbatim**, and refuses to run if they have drifted.

## Why `verbatim/` is not in this repository

Every one of those files is marked `POINT PREDICTIVE © Confidential Material`. They are
deliberately **git-ignored** — both the originals under `docs/` and the byte-identical
working copies under `prompts/verbatim/`. Nothing that is customer-confidential is pushed
to a shared remote; the files travel through the customer's own channel.

Consequence: a fresh clone has `prompts/loader.py`, `prompts/manifest.json` and the tests,
but no `prompts/verbatim/`, so importing `prompts` raises `PromptIntegrityError` naming the
first missing file. That is intended — the alternative is code that silently substitutes
paraphrased text, which is the exact defect this package exists to prevent.

## Restoring it

Put the customer's files in `docs/` with their original names, then:

```bash
# .rtf orchestration docs -> the .txt the loader reads
mkdir -p docs/.converted
for f in docs/*_orchestration*.rtf; do
  textutil -convert txt "$f" -output "docs/.converted/$(basename "${f%.rtf}").txt"
done

python3 -c "from prompts.bootstrap import sync; sync()"   # copies + rewrites manifest.json
MOCK_MODE=1 python3 -m pytest tests/test_prompt_fidelity.py -q
```

The expected files are listed in `prompts/manifest.json` (`SOURCES` maps each packaged
name to its `docs/` origin), so the manifest doubles as the inventory of what to obtain.

## The guarantee

- `verbatim/` is a **byte-for-byte** copy — `shutil.copyfile`, never retyped.
- `loader.py` sha256-checks every file at import and raises on any mismatch.
- `tests/test_prompt_fidelity.py` additionally diffs `verbatim/` against `docs/`, so
  regenerating the manifest to cover an edit does **not** get past CI. Five mutation
  attempts, including that one, were verified to fail.
- No prompt text appears in any Python literal in this package.
