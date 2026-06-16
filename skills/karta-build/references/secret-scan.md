# Secret Scan

## What it does

karta runs a secret scan **before each commit** during build. The scan inspects the staged diff — only the changes about to be committed, not the full working tree — and blocks the commit if it finds a credential, token, key, or other secret pattern.

## On a hit

When the scan finds a match it blocks the commit and surfaces the finding: the file, line, and matched pattern. The build halts at that item; no commit is written. The item is marked failed with the scan output attached. Resolution requires removing or rotating the secret before the item can be retried.

## Allow-list

Some matches are benign — test fixtures, example placeholders, documentation snippets. These are recorded in-repo in an allow-list file (e.g. `.karta/secret-scan-allowlist`) as path/pattern entries:

```
# path glob : pattern description
tests/fixtures/** : example-api-key
docs/examples/*.md : placeholder-token
```

An entry suppresses the matching finding for the matched path. Allow-list entries are reviewed alongside the code that adds them — they are part of the commit record, not a silent bypass.

## Scope

The secret scan is a floor safety check. It catches accidental credential commits during the build loop. It is not a replacement for the project's own secret tooling (e.g. git-secrets, trufflehog, detect-secrets configured in CI). Projects with existing secret scanning should treat karta's check as an additional early gate, not a substitute.
