#!/usr/bin/env bash
set -euo pipefail

# Release helper for spec 023 — Red Hat Enterprise Linux Compatibility
# Usage: bash scripts/release-023.sh

BRANCH=$(git branch --show-current)
if [[ -z "$BRANCH" ]]; then
  echo "ERROR: could not determine current git branch"
  exit 1
fi

if [[ "$BRANCH" != feat/* && "$BRANCH" != fix/* && "$BRANCH" != chore/* ]]; then
  echo "ERROR: current branch must be feat/*, fix/*, or chore/*; got: $BRANCH"
  exit 1
fi

TYPE="fix"
SCOPE="runtime"
SUBJECT="$TYPE($SCOPE): Red Hat Enterprise Linux Compatibility"
COMMIT_BODY="Implements memory/specs/023-redhat-compatibility.md
All ACs implemented, tests passing, security reviewed, docs updated."

# Determine latest tag and compute new tag (patch bump for fix)
LATEST_TAG=$(git tag --sort=-v:refname | head -n1 || true)
if [[ -z "$LATEST_TAG" ]]; then
  LATEST_TAG="v0.0.0"
fi

NEW_TAG=$(python3 - <<PY
import sys
latest=sys.argv[1].lstrip('v')
parts=list(map(int, latest.split('.')))
# patch bump
parts[2]+=1
print('v%d.%d.%d' % tuple(parts))
PY
"$LATEST_TAG")

echo "Latest tag: $LATEST_TAG -> New tag: $NEW_TAG"

# Commit changes
git add -A
if git diff --staged --quiet; then
  echo "No staged changes to commit. Proceeding."
else
  git commit -m "$SUBJECT

$COMMIT_BODY"
fi

# Push branch
git push -u origin "$BRANCH"

# Prepare PR body
PR_BODY=$(cat <<EOF
## Root cause
Prometheus currently runs on macOS (Metal GPU, Podman Desktop). The target deployment is two bare-metal RHEL 9.7 servers with the following profile.

## Impact
- scripts/install-rhel.sh
- scripts/validate.sh
- .env.redhat.example
- gateway/.env.podman.example
- auth-service/.env.example

## Risks
- The installer generates secrets and writes them into host .env files and /etc/prometheus; verify file ownership and permissions.

## Validations run
- All ACs implemented ✓
- Pre-push hook suite passed (ruff / mypy / pytest) ✓
- security-reviewer-agent: no CRITICAL/HIGH findings ✓
- human-approved ✓

Implements: memory/specs/023-redhat-compatibility.md
EOF
)

# Create PR: feature -> develop
echo "Creating PR $BRANCH -> develop..."
gh pr create --base develop --head "$BRANCH" --title "$SUBJECT" --body "$PR_BODY"

# Merge PR feature -> develop (squash and delete branch)
echo "Merging PR $BRANCH -> develop..."
gh pr merge --squash --delete-branch

# Create PR: develop -> main
RELEASE_TITLE="release: ${NEW_TAG} — Red Hat Enterprise Linux Compatibility"

echo "Creating PR develop -> main..."
gh pr create --base main --head develop --title "$RELEASE_TITLE" --body "$PR_BODY

Release: ${NEW_TAG}"

# Merge PR develop -> main
echo "Merging PR develop -> main..."
gh pr merge --merge

# Tag and push the release
echo "Tagging release $NEW_TAG..."
git checkout main
git pull origin main
git tag -a "$NEW_TAG" -m "Release $NEW_TAG — Red Hat Enterprise Linux Compatibility"
git push origin "$NEW_TAG"

# Create GitHub release
echo "Creating GitHub release $NEW_TAG..."
RELEASE_NOTES=$(cat <<EOF
## What's new
- RHEL-specific installer and validation scripts
- RHEL .env templates and gateway/auth examples

## Acceptance criteria delivered
- All ACs from memory/specs/023-redhat-compatibility.md are implemented

## Spec
memory/specs/023-redhat-compatibility.md
EOF
)

gh release create "$NEW_TAG" --title "${NEW_TAG} — Red Hat Enterprise Linux Compatibility" --notes "$RELEASE_NOTES"

# Update spec: mark released, then closed (append pipeline-log entries)
python3 - <<'PY'
import sys, yaml
from datetime import date
p='memory/specs/023-redhat-compatibility.md'
with open(p) as f:
    content=f.read()
parts=content.split('---')
if len(parts) < 3:
    print('Unexpected spec format')
    sys.exit(1)
meta=yaml.safe_load(parts[1])
# mark released
meta['status']='released'
meta['pipeline-log'].append({'agent':'release-agent','status':'released','timestamp':date.today().isoformat()})
# write back
new_fm='---\n'+yaml.safe_dump(meta, sort_keys=False).strip()+"\n---\n"
rest=''.join(parts[2:])
with open(p,'w') as f:
    f.write(new_fm+rest)
# then mark closed
with open(p) as f:
    content=f.read()
parts=content.split('---')
meta=yaml.safe_load(parts[1])
meta['status']='closed'
meta['current-agent']=''
meta['pipeline-log'].append({'agent':'release-agent','status':'closed','timestamp':date.today().isoformat()})
new_fm='---\n'+yaml.safe_dump(meta, sort_keys=False).strip()+"\n---\n"
rest=''.join(parts[2:])
with open(p,'w') as f:
    f.write(new_fm+rest)
print('Spec updated to released and closed')
PY

echo "Release complete: $NEW_TAG"

echo "Done."
