#!/usr/bin/env bash
# Releases house-backend (PLAN D-5): checks, builds the wheel, commits the
# version, tags vX.Y.Z, pushes, and uploads house_backend-X.Y.Z-py3-none-any.whl
# to the GitHub Release that apps pin (D-4). dist/ is never committed.
#
#   ./release.sh 1.0.0            release
#   ./release.sh --dry-run 1.0.0  everything up to the build, then undo the version bump
set -euo pipefail
cd "$(dirname "$0")"

dry=0
if [[ "${1:-}" == "--dry-run" ]]; then dry=1; shift; fi
version="${1:-}"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "usage: ./release.sh [--dry-run] X.Y.Z" >&2
  exit 2
fi
tag="v$version"
whl="house_backend-$version-py3-none-any.whl"
repo="kenjabber/house-backend"
version_file="house_backend/__init__.py"
python="${PYTHON:-.venv/bin/python}"
fail() { echo "release: $*" >&2; exit 1; }

[[ -x "$python" ]] || fail "no $python; make the virtualenv first (see USAGE.md) or set PYTHON"
[[ "$(git branch --show-current)" == main ]] || fail "not on main"
[[ -z "$(git status --porcelain)" ]] || fail "the working tree has changes; commit them first"
git fetch --quiet origin
[[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]] || fail "main is not the same as origin/main"
git rev-parse -q --verify "refs/tags/$tag" >/dev/null && fail "tag $tag exists"
git ls-remote --exit-code --tags origin "refs/tags/$tag" >/dev/null && fail "tag $tag exists on origin"

# Apps download the wheel with no credentials (D-3), so the repo must be public.
visibility="$(gh repo view "$repo" --json visibility -q .visibility)"
if [[ "$visibility" != PUBLIC ]]; then
  if (( dry )); then echo "release: note: $repo is $visibility; a real release needs it public (D-3)"
  else fail "$repo is $visibility; apps cannot download a private release (D-3). Make it public first."; fi
fi

echo "== checks"
"$python" -m pip install --quiet -e '.[mcp,test]' build ruff
"$python" -m ruff check .
"$python" -m pytest -q

echo "== build $tag"
# Until the release commit, a failure puts the version back.
trap 'git checkout -- "$version_file"; rm -rf dist' EXIT
sed -i.bak -E "s/^__version__ = \".*\"$/__version__ = \"$version\"/" "$version_file" && rm -f "$version_file.bak"
grep -q "^__version__ = \"$version\"$" "$version_file" || fail "could not set the version in $version_file"
rm -rf dist
"$python" -m build --wheel --outdir dist >/dev/null
[[ -f "dist/$whl" ]] || fail "the build did not write dist/$whl"
"$python" -m zipfile -l "dist/$whl" | grep -q 'house_backend/py.typed' || fail "dist/$whl has no house_backend/py.typed"
echo "built $whl ($(du -h "dist/$whl" | cut -f1 | tr -d ' '))"

if (( dry )); then
  echo "dry run: stopped before commit, tag, push and upload"
  exit 0
fi

echo "== publish $tag"
trap - EXIT
git commit --quiet -am "Release $tag"
git tag -a "$tag" -m "house-backend $tag"
git push --atomic origin main "refs/tags/$tag"
gh release create "$tag" "dist/$whl" --repo "$repo" --title "house-backend $tag" --generate-notes
rm -rf dist

url="https://github.com/$repo/releases/download/$tag/$whl"
curl -sSfLo /dev/null "$url" || fail "uploaded, but $url does not download without credentials"
echo
echo "Released $tag. Apps pin it in backend/pyproject.toml with:"
echo "  \"house-backend[mcp] @ $url\""
