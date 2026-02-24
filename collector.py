# collector.py
import requests
import subprocess
import json
import os
import re
from pathlib import Path

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}

def get_bug_issues(owner, repo, max_issues=50):
    """Fetch closed bug issues that were fixed by a PR."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    params = {
        "state": "closed",
        "labels": "bug",
        "per_page": min(max_issues, 100),
        "sort": "updated",
        "direction": "desc"
    }
    resp = requests.get(url, headers=HEADERS, params=params)
    resp.raise_for_status()
    # Filter out pull requests (GitHub API returns PRs as issues too)
    return [i for i in resp.json() if "pull_request" not in i]


def find_closing_pr(owner, repo, issue_number):
    """Find the PR that closed this issue using the Timeline API."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/timeline"
    headers = {**HEADERS, "Accept": "application/vnd.github.mockingbird-preview+json"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()

    for event in resp.json():
        # "cross-referenced" events link to PRs that mention the issue
        if event.get("event") == "cross-referenced":
            source = event.get("source", {}).get("issue", {})
            if source.get("pull_request") and source.get("state") == "closed":
                return source["number"]

        # "connected" or "closed" events directly link a PR
        if event.get("event") == "closed" and event.get("commit_id"):
            # Find PR containing this commit
            commit_sha = event["commit_id"]
            pr_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{commit_sha}/pulls"
            pr_resp = requests.get(pr_url, headers=HEADERS)
            if pr_resp.ok and pr_resp.json():
                return pr_resp.json()[0]["number"]

    return None


def get_pr_details(owner, repo, pr_number):
    """Get PR metadata, merge commit, and diff."""
    # PR info
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    pr = requests.get(url, headers=HEADERS).json()

    # PR files changed (the actual diff)
    files_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files"
    files = requests.get(files_url, headers=HEADERS).json()

    # PR commits
    commits_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/commits"
    commits = requests.get(commits_url, headers=HEADERS).json()

    return {
        "pr_number": pr_number,
        "title": pr.get("title"),
        "body": pr.get("body"),
        "merge_commit_sha": pr.get("merge_commit_sha"),
        "base_sha": pr.get("base", {}).get("sha"),      # branch base
        "head_sha": pr.get("head", {}).get("sha"),       # last commit in PR
        "files_changed": [
            {
                "filename": f["filename"],
                "status": f["status"],           # added, modified, removed
                "patch": f.get("patch", ""),      # the diff
                "additions": f["additions"],
                "deletions": f["deletions"],
            }
            for f in files
        ],
        "commits": [c["sha"] for c in commits],
    }


def get_commit_before_fix(owner, repo, merge_commit_sha):
    """Get the parent commit (state before the fix was applied)."""
    url = f"https://api.github.com/repos/{owner}/{repo}/commits/{merge_commit_sha}"
    resp = requests.get(url, headers=HEADERS).json()
    parents = resp.get("parents", [])
    if parents:
        return parents[0]["sha"]  # first parent = mainline before merge
    return None


def get_file_content_at_commit(owner, repo, filepath, commit_sha):
    """Fetch file content at a specific commit."""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{filepath}?ref={commit_sha}"
    resp = requests.get(url, headers=HEADERS)
    if resp.status_code == 200:
        import base64
        content = resp.json().get("content", "")
        return base64.b64decode(content).decode("utf-8", errors="replace")
    return None


def collect_bug_data(owner, repo, max_issues=20):
    """Main collection pipeline."""
    issues = get_bug_issues(owner, repo, max_issues)
    results = []

    for issue in issues:
        issue_number = issue["number"]
        print(f"Processing issue #{issue_number}: {issue['title']}")

        # Find closing PR
        pr_number = find_closing_pr(owner, repo, issue_number)
        if not pr_number:
            print(f"  ⏭ No closing PR found, skipping")
            continue

        # Get PR details
        pr = get_pr_details(owner, repo, pr_number)
        if not pr["merge_commit_sha"]:
            print(f"  ⏭ PR not merged, skipping")
            continue

        # Get commit before fix
        pre_fix_sha = get_commit_before_fix(owner, repo, pr["merge_commit_sha"])

        # Get file contents BEFORE and AFTER the fix
        files_context = []
        for f in pr["files_changed"]:
            before_content = get_file_content_at_commit(
                owner, repo, f["filename"], pre_fix_sha
            ) if pre_fix_sha else None

            after_content = get_file_content_at_commit(
                owner, repo, f["filename"], pr["merge_commit_sha"]
            )

            files_context.append({
                "filename": f["filename"],
                "patch": f["patch"],
                "before_fix": before_content,   # buggy version
                "after_fix": after_content,      # fixed version
            })

        result = {
            "issue_number": issue_number,
            "issue_title": issue["title"],
            "issue_body": issue.get("body", ""),
            "pr_number": pr_number,
            "pr_title": pr["title"],
            "pr_body": pr.get("body", ""),
            "merge_commit_sha": pr["merge_commit_sha"],
            "pre_fix_sha": pre_fix_sha,
            "files": files_context,
        }
        results.append(result)
        print(f"  ✓ Collected: PR #{pr_number}, {len(files_context)} files")

    return results


if __name__ == "__main__":
    import sys
    owner, repo = sys.argv[1].split("/")  # e.g., "facebook/react"
    data = collect_bug_data(owner, repo)

    output_path = Path("collected_bugs.json")
    output_path.write_text(json.dumps(data, indent=2))
    print(f"\n✅ Collected {len(data)} bugs → {output_path}")