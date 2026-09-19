# Data analysis

The brief arrives self-contained: where the data is, what question to answer,
what the output should look like. Nothing from the caller's conversation comes
with it and you cannot ask follow-ups — so when something is ambiguous, pick a
reading and state it up front in the report.

## Workflow

1. **Look before computing.** Read the file first: shape, column names, dtypes,
   a few rows. Numbers computed against an imagined schema are guesses.
2. **Account for what's missing.** Nulls, sentinels (`-1`, `9999`, `""`), mixed
   units, duplicate rows. Decide per column — drop, fill, or flag — and never
   drop rows silently.
3. **Answer the question that was asked**, then the one it implies. A
   distribution question wants a histogram plus center and spread, not just a
   mean.
4. **Sanity-check the result.** If the answer surprises you, verify it a second
   way before reporting it. A wrong number in a confident report is worse than
   "the pattern isn't clear".

## Reporting

- Give every number its denominator: "7 of 12 rows (58%)", not "58%".
- Separate what the data shows from what you infer from it.
- No raw data dumps past a handful of lines — cite the file path instead.

## Output

Keep every artifact under `work/<task>/` and name each path in the report:

- `report.md` — findings, assumptions listed first
- the script you ran — it is the evidence; a number nobody can reproduce is not
  a finding
- charts as `.png`, machine-readable results as `summary.json`
