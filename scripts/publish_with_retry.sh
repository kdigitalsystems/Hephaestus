#!/usr/bin/env bash
# Push the current branch to origin/main, retrying transient failures.
#
# On 2026-09-15 a completed run's push was rejected with "remote: Internal Server Error"
# and the whole day's work was discarded, because the next run resets to origin/main.
# Each retry rebases first, so a push that raced with the other publisher recovers the
# same way. A rebase conflict still aborts without publishing.
set -euo pipefail

attempts="${PUSH_ATTEMPTS:-3}"
backoff="${PUSH_BACKOFF_SECONDS:-20}"

for attempt in $(seq 1 "$attempts"); do
  if git push origin main; then
    exit 0
  fi
  if [ "$attempt" -eq "$attempts" ]; then
    break
  fi
  delay=$((backoff * attempt))
  echo "Push attempt $attempt of $attempts failed; refreshing and retrying in ${delay}s."
  sleep "$delay"
  if ! git pull --rebase origin main; then
    git rebase --abort 2>/dev/null || true
    echo "Rebase onto origin/main failed after a push error; not publishing."
    exit 1
  fi
done

echo "Could not publish after $attempts attempts."
exit 1
