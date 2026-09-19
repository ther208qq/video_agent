# Sandbox execution

The sandbox is a persistent Linux container. Files you write there survive
between tool calls in a session, but not across sessions.

Two tools, split by how long the code lives:

- **Bash** — anything you will run more than once, plus the shell itself:
  installing packages, moving files, running a script you wrote to disk,
  chaining commands with pipes.
- **ExecuteCode** — disposable one-shots: a quick calculation, a small
  transform, a sanity check. Not for anything iterative or reusable — write
  that to a file and run it with Bash.

## Paths

Work in relative paths (`work/<task>/`, `data/`). A leading slash writes
outside the workspace and the file will not persist.

## Timeouts

`Bash` takes a `timeout` in **milliseconds**, default 120000 (2 min), max
600000 (10 min). Raise it for installs, downloads, and long renders — a
command that hits the limit is killed, not paused.
