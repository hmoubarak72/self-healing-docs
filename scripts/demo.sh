#!/bin/sh
# Simulates a pull request against examples/sample_project and runs the Action
# entrypoint exactly as GitHub would (minus the API token), printing the PR comment.
set -e
git config --global --add safe.directory '*'
git config --global user.email demo@docheal.dev
git config --global user.name docheal-demo
WORK=$(mktemp -d)
cp -r /src/examples/sample_project "$WORK/repo"
cd "$WORK/repo"
rm -rf .docheal
git init -q -b main && git add -A && git commit -qm "base"
git checkout -qb feature/retries
# 1) simple change -> auto-fix     2) removed capability -> human review
sed -i 's/max_retries: int = 3/max_retries: int = 5/' taskq/client.py
sed -i 's/def get_status(/def fetch_status(/; s/self.get_status(task_id)/self.fetch_status(task_id)/' taskq/client.py
python - <<'PY'
import re, pathlib
p = pathlib.Path("taskq/api.py")
p.write_text(re.sub(r'@app\.delete\("/tasks/\{task_id\}"\)\ndef cancel_task.*?\n\n?(?=\Z|@)', "", p.read_text(), flags=re.S))
PY
# comment-only noise that must be ignored
sed -i 's/        body = {/        # build the request body\n        body = {/' taskq/client.py
git commit -qam "Rename get_status, bump retries, drop cancel endpoint"
cat > "$WORK/event.json" <<JSON
{"pull_request": {"number": 42, "base": {"sha": "main", "ref": "main"},
 "head": {"sha": "$(git rev-parse HEAD)", "ref": "feature/retries", "repo": {"full_name": "demo/taskq"}}}}
JSON
export GITHUB_EVENT_PATH="$WORK/event.json" GITHUB_REPOSITORY=demo/taskq GITHUB_WORKSPACE="$WORK/repo" \
       GITHUB_OUTPUT="$WORK/outputs.txt" GITHUB_STEP_SUMMARY="$WORK/summary.md"
docheal action
echo "================ PR COMMENT ================"
cat "$WORK/summary.md"
echo "================ ACTION OUTPUTS ================"
grep -A1 -E '^(stale-count|auto-fixed-count|flagged-count)<<' "$WORK/outputs.txt" | grep -v -- '--' | paste - -
echo "================ AUTO-FIX PREVIEW ================"
docheal check --repo . --base main --head HEAD --apply --no-index >/dev/null 2>&1 || true
git --no-pager diff --stat -- docs README.md
git --no-pager diff -- docs README.md
