#!/usr/bin/env bash
# Release see: bump the version, merge it through a PR once CI passes, tag it, and update
# the Homebrew formula in the tap.
#
#   scripts/release.sh 0.4.0
#
# Needs: gh (logged in), a clean main checkout, and the tap checked out next to this repo
# (../homebrew-tap) or at $TAP_DIR. The "## Unreleased" heading in CHANGELOG.md becomes
# the new version's heading.
set -euo pipefail

version="${1:-}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "usage: $0 X.Y.Z" >&2; exit 2; }
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
tap_dir="${TAP_DIR:-$repo_dir/../homebrew-tap}"
tag="v$version"
cd "$repo_dir"

step() { printf '\n==> %s\n' "$*"; }

step "Checking the working copies"
[[ -z "$(git status --porcelain)" ]] || { echo "see has uncommitted changes" >&2; exit 1; }
[[ -f "$tap_dir/Formula/see.rb" ]] || { echo "tap not found at $tap_dir" >&2; exit 1; }
[[ -z "$(git -C "$tap_dir" status --porcelain)" ]] || { echo "the tap has uncommitted changes" >&2; exit 1; }
git checkout -q main && git pull -q --ff-only
git -C "$tap_dir" pull -q --ff-only
if git rev-parse -q --verify "refs/tags/$tag" >/dev/null || git ls-remote --exit-code --tags origin "$tag" >/dev/null; then
  echo "$tag already exists" >&2; exit 1
fi
grep -q '^## Unreleased' CHANGELOG.md || { echo "CHANGELOG.md has no '## Unreleased' section" >&2; exit 1; }

step "Bumping the version to $version"
branch="release-$version"
git checkout -q -b "$branch"
sed -i.bak -E "s/^version = \"[^\"]+\"/version = \"$version\"/" pyproject.toml
sed -i.bak -E "s/^__version__ = \"[^\"]+\"/__version__ = \"$version\"/" src/see/__init__.py
sed -i.bak "s/^## Unreleased$/## $version — $(date +%Y-%m-%d)/" CHANGELOG.md
rm -f pyproject.toml.bak src/see/__init__.py.bak CHANGELOG.md.bak
git commit -q -am "Version $version"
git push -q -u origin "$branch"
pr_url="$(gh pr create --title "Version $version" --body "Release $version. See CHANGELOG.md.")"
echo "$pr_url"

step "Waiting for CI"
sleep 10
gh pr checks "$pr_url" --watch --interval 15 --fail-fast

step "Merging and tagging $tag"
gh pr merge "$pr_url" --squash --delete-branch
git checkout -q main && git pull -q --ff-only
git branch -D "$branch" 2>/dev/null || true
git tag -a "$tag" -m "see $version"
git push -q origin "$tag"

step "Updating the Homebrew formula"
tarball="https://github.com/Youkin06/see/archive/refs/tags/$tag.tar.gz"
sha="$(curl -fsSL "$tarball" | shasum -a 256 | cut -d' ' -f1)"
formula="$tap_dir/Formula/see.rb"
sed -i.bak -E "s|^  url \".*\"|  url \"$tarball\"|; s|^  sha256 \".*\"|  sha256 \"$sha\"|" "$formula"
rm -f "$formula.bak"
grep -q "$tag" "$formula" && grep -q "$sha" "$formula" || { echo "formula update failed" >&2; exit 1; }
git -C "$tap_dir" commit -q -am "see $version"
git -C "$tap_dir" push -q

step "Done: see $version"
echo "Users can now run: brew upgrade see   (or brew install youkin06/tap/see)"
