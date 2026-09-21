# Short video analysis

Content Understanding for a single short video: turn its description and
transcript into one structured `ContentFeatures` record. The job stops there.

## What to do

Call `analyze_content` once per video, passing that video's own `video_id`,
`description`, `transcript` and `duration_ms`. It returns a validated
`ContentFeatures`:

- `has_hook`, `has_question`, `has_conflict`, `has_reversal` — booleans
- `emotion_score` — integer 1-10
- `question_count` — non-negative integer
- `topic` — short summary

## Rules

- **Never invent features.** Every field must be derived from the text you pass
  in. Feed the tool what the video actually has; if `transcript` is empty, say
  the material is thin rather than guessing what the video probably shows.
- **Report what came back.** Do not re-interpret the result or upgrade a `false`
  to a `true` because the video "felt" like it had a hook.
- **Out of scope:** comparing videos, computing engagement rates, drawing
  conclusions across a dataset, building reports. That is a later stage — say it
  is out of scope instead of attempting it here.
