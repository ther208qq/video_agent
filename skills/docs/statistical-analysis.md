# Statistical analysis

Look for statistical relationships between a short video's content features and
its engagement metrics. Two analyses are supported:

| feature type | target type | tool |
|---|---|---|
| binary | numeric | `group_comparison` |
| numeric | numeric | `correlation_analysis` |

Pick the tool from the **shape of the variables in the question**, not from the
wording. Both tools take the same three arguments: `dataset` (the records to
analyze, one dict per video), `feature`, and `target`.

## Method 1 — group comparison (binary + numeric)

Use when the question contrasts videos that *have* a feature against those that
*don't*: "do videos with a hook do better?", "is there a difference between..."

- `feature` must be one of the booleans: `has_hook`, `has_question`,
  `has_conflict`, `has_reversal`.
- `target` must be one of the rates: `like_rate`, `comment_rate`, `share_rate`.

Examples: `has_hook` → `like_rate`, `has_question` → `comment_rate`,
`has_conflict` → `share_rate`, `has_reversal` → `like_rate`.

The tool splits the records into a `true` group and a `false` group, reports each
group's size, mean and median plus the mean difference, and runs Welch's t-test.
It returns a structured `GroupComparison` holding those numbers.

## Method 2 — correlation (numeric + numeric)

Use when the question asks whether two *numeric* variables move together:
"is emotion related to like rate?", "does the number of questions correlate
with anything?", "as X goes up, does Y?"

- `feature` and `target` are both numeric. Usable values: `emotion_score`,
  `question_count`, `like_rate`, `comment_rate`, `share_rate`.

Examples: `emotion_score` → `like_rate`, `question_count` → `like_rate`,
`emotion_score` → `share_rate`.

The tool drops rows where either field is missing, then computes the Pearson
correlation. It returns a structured `CorrelationAnalysis` holding `n`, the
`correlation`, and the `p_value`.

## Choosing between them

- "Do videos **with** feature X differ from those **without** it?" → the feature
  is a boolean → **group_comparison**.
- "Are these **two numeric variables** related?" → both are numeric →
  **correlation_analysis**.

If the feature the user names is a boolean (`has_*`), use group comparison. If it
is a count or a score (`emotion_score`, `question_count`), use correlation.
Never pass a boolean to `correlation_analysis` or a numeric variable to
`group_comparison` — both tools reject the wrong shape and the error is a sign
you picked the wrong method.

## Rules

- **Never compute statistics yourself.** No averaging by hand, no eyeballing the
  data, no estimating a p-value or a correlation. Every number in your answer
  must come from the tool's artifact (`GroupComparison` or
  `CorrelationAnalysis`).
- **The tool artifact is the source of truth.** Report the numbers exactly as
  returned. If your own reading of the data disagrees, the tool wins.
- **Never invent data.** Do not make up rows, sample sizes, effect sizes,
  correlations or p-values. If the tool errors — a feature is not binary, a
  group is too small, a field is missing, a variable has no variance — report
  the error as it is instead of answering anyway.
- **Report the numbers as given.** Do not upgrade a `p_value` of 0.2 into "a
  slight trend" or downgrade a significant result because it seems unlikely.
- **`p >= 0.05` is not statistically significant.** Say there is no significant
  relationship; do not call it a trend, a weak effect, or "marginally
  significant".
- **Correlation is not causation.** A relationship between a content feature and
  engagement does not mean the feature caused the engagement. Report it as an
  association only, and do not suggest the creator should change their content.
- **A categorical variable is not numeric.** Do not pass `topic` (or any other
  label) as `feature` or `target`, and do not encode it as 0/1 to force it
  through. `topic` values are near-unique, so no such encoding is meaningful.

## Out of scope in this version

Regression, correlation matrix, visualization, causal inference, and any
multi-feature or multivariate analysis. Say it is out of scope rather than
improvising an approximation of it.
