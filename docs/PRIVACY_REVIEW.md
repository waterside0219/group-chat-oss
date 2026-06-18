# Privacy Review Checklist

Use this checklist before publishing changes from a private deployment into
`group-chat-oss`.

## Included In This OSS Sync

- Neutral workgroup protocol support:
  - `chat`
  - `task`
  - `review_request`
  - `question`
  - `broadcast`
- ACK and delivery tracking.
- `review_request` threads with P0/P1/P2 comments and `ALL_CLEAR`.
- `groupchat-ack-reminder` dry-run/apply reminder command.
- Neutral tmux dispatcher improvements.
- SwiftUI sample with the five message kinds.
- Public documentation for work rooms, casual rooms, review loops, and task
  boards.

## Must Not Be Published

- Real JSONL chat logs.
- Real state files.
- API tokens, APNs keys, tunnel tokens, webhook secrets, model API keys, or
  private keys.
- Private hostnames, IP addresses, personal emails, or real member names.
- Private app bundle ids, provisioning data, or production deployment paths.
- Backups, incident reports, migration logs, screenshots, and local test data.

## Local Ignore Coverage

The repository `.gitignore` excludes:

- `.env`
- `*.secret`
- `config.toml`
- `*.local.toml`
- `data/`
- `*.jsonl`
- `__pycache__/`
- `*.pyc`
- `.DS_Store`

## Manual Scan Commands

Run from the repository root:

```bash
grep -RInE --exclude-dir=.git --exclude-dir=__pycache__ \
  '(authorization token pattern)|(API key prefix)|(private key block marker)' .

grep -RInE --exclude-dir=.git --exclude-dir=__pycache__ \
  '([0-9]{1,3}\.){3}[0-9]{1,3}|@[A-Za-z0-9._%+-]+\\.[A-Za-z]{2,}' .
```

Expected result: no real secrets or private identifiers. Review false positives
manually before pushing.
