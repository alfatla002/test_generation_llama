# 🐛 Bug2Test — Automated Regression Test Generator

**Collect GitHub bug issues → Generate tests with Claude AI → Verify fail-to-pass behavior**

Bug2Test is an automated pipeline that mines closed bug reports from GitHub repositories, generates targeted regression tests using Anthropic's Claude API, and verifies that each test correctly **fails on the buggy code** and **passes after the fix** — the gold standard for regression testing.

---

## How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│                        Bug2Test Pipeline                        │
│                                                                 │
│  ┌───────────┐    ┌──────────────┐    ┌──────────────────────┐  │
│  │  Collect   │───▶│   Generate   │───▶│       Verify         │  │
│  │            │    │              │    │                      │  │
│  │ GitHub API │    │  Claude API  │    │  git checkout buggy  │  │
│  │ • Issues   │    │  • Analyzes  │    │  → run test → FAIL ✗ │  │
│  │ • PRs      │    │    bug + fix │    │                      │  │
│  │ • Commits  │    │  • Generates │    │  git checkout fixed  │  │
│  │ • Diffs    │    │    test code │    │  → run test → PASS ✓ │  │
│  │ • Code     │    │              │    │                      │  │
│  └───────────┘    └──────────────┘    └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### Phase 1: Collect

Queries the GitHub API to find closed bug issues that were resolved by a merged pull request. For each bug it collects:

- **Issue metadata** — title, description, labels
- **Closing PR** — identified via the Timeline API
- **Commits** — the merge commit (fix) and its parent (pre-fix / buggy state)
- **Diffs** — exact code changes introduced by the fix
- **File contents** — full source code before and after the fix

### Phase 2: Generate

Sends the collected context to Claude, which analyzes the bug report, the code diff, and the before/after file contents to produce a test that exercises the exact buggy behavior.

### Phase 3: Verify

Clones the repository locally and runs a two-step verification:

1. **Checkout pre-fix commit** → inject test → run → **expect FAIL**
2. **Checkout fix commit** → run same test → **expect PASS**

Only tests that achieve this **fail-to-pass** transition are considered successful.

---

## Prerequisites

- **Python 3.9+**
- **Git** installed and available in PATH
- **GitHub Personal Access Token** with `repo` scope
- **Anthropic API Key** — get one at [console.anthropic.com](https://console.anthropic.com)

### Language-Specific (depending on target repo)

| Target Repo Language    | Requirements                      |
| ----------------------- | --------------------------------- |
| Python                  | `pytest` installed                |
| JavaScript / TypeScript | `node`, `npm`, `jest` or `vitest` |
| Go                      | Go toolchain installed            |

---

## Installation

```bash
# Clone this project
git clone https://github.com/your-username/bug2test.git
cd bug2test

# Install Python dependencies
pip install requests anthropic

# Set environment variables
export GITHUB_TOKEN="ghp_your_token_here"
export ANTHROPIC_API_KEY="sk-ant-your_key_here"
```

---

## Quick Start

### Run the full pipeline

```bash
# Target any public GitHub repo with bug-labeled issues
./run_pipeline.sh owner/repo

# Examples:
./run_pipeline.sh pallets/flask
./run_pipeline.sh fastapi/fastapi
./run_pipeline.sh expressjs/express
```

### Run each step individually

```bash
# Step 1: Collect bug data from GitHub
python collector.py owner/repo

# Step 2: Generate tests using Claude
python test_generator.py

# Step 3: Verify fail-to-pass on local clones
python verify_tests.py https://github.com/owner/repo.git
```

---

## Project Structure

```
bug2test/
├── README.md
├── run_pipeline.sh          # Orchestrator script
├── collector.py             # GitHub data collection
├── test_generator.py        # Claude API test generation
├── verify_tests.py          # Fail-to-pass verification runner
│
├── collected_bugs.json      # (generated) Raw collected data
├── generated_tests/         # (generated) Claude-generated test files
│   ├── test_issue_123.py
│   ├── test_issue_456.py
│   └── manifest.json        # Maps tests to commits
├── workspace/               # (generated) Cloned repos for verification
│   ├── repo_issue_123/
│   └── repo_issue_456/
└── verification_results.json # (generated) Final results
```

---

## Output Format

### collected_bugs.json

```json
[
  {
    "issue_number": 1234,
    "issue_title": "KeyError when parsing empty config",
    "issue_body": "When config file is empty, app crashes with...",
    "pr_number": 1240,
    "pr_title": "Fix empty config handling",
    "merge_commit_sha": "abc123...",
    "pre_fix_sha": "def456...",
    "files": [
      {
        "filename": "src/config.py",
        "patch": "@@ -45,6 +45,8 @@ ...",
        "before_fix": "... full file content (buggy) ...",
        "after_fix": "... full file content (fixed) ..."
      }
    ]
  }
]
```

### verification_results.json

```json
[
  {
    "issue": 1234,
    "status": "FAIL_TO_PASS ✅",
    "pre_fix_failed": true,
    "post_fix_passed": true
  },
  {
    "issue": 5678,
    "status": "PASS_TO_PASS ❌ (test doesn't catch the bug)",
    "pre_fix_failed": false,
    "post_fix_passed": true
  }
]
```

### Possible verification outcomes

| Status            | Pre-fix | Post-fix | Meaning                                  |
| ----------------- | ------- | -------- | ---------------------------------------- |
| `FAIL_TO_PASS ✅` | FAIL    | PASS     | Test correctly catches the bug           |
| `PASS_TO_PASS ❌` | PASS    | PASS     | Test doesn't exercise the buggy behavior |
| `FAIL_TO_FAIL ❌` | FAIL    | FAIL     | Test is broken or has unrelated failures |
| `PASS_TO_FAIL ❌` | PASS    | FAIL     | Possible regression in the fix itself    |

---

## Configuration

### Environment Variables

| Variable            | Required | Description                                    |
| ------------------- | -------- | ---------------------------------------------- |
| `GITHUB_TOKEN`      | Yes      | GitHub personal access token with `repo` scope |
| `ANTHROPIC_API_KEY` | Yes      | Anthropic API key for Claude                   |

### Tunable Parameters

Edit directly in the scripts or pass as arguments:

| Parameter    | Location            | Default                    | Description                   |
| ------------ | ------------------- | -------------------------- | ----------------------------- |
| `max_issues` | `collector.py`      | 20                         | Max bug issues to collect     |
| `model`      | `test_generator.py` | `claude-sonnet-4-20250514` | Claude model to use           |
| `max_tokens` | `test_generator.py` | 4096                       | Max tokens for generated test |
| `timeout`    | `verify_tests.py`   | 120s                       | Timeout per test run          |

---

## Tips for Best Results

### Choosing a good target repo

- Repos that label bugs consistently (e.g., `bug`, `defect`, `fix`) yield more results.
- Small, well-scoped PRs (1–3 files changed) produce better tests.
- Repos with an established test suite help Claude match the testing style.

### Improving generation quality

- **Smaller context wins**: the pipeline truncates large files to 3,000 chars. For large files, consider extracting only the relevant functions/classes.
- **Retry with feedback**: if a test doesn't achieve fail-to-pass, send the test output back to Claude for a second attempt.
- **Model selection**: use `claude-sonnet-4-20250514` for speed/cost or `claude-opus-4-6` for the hardest bugs.

### Common issues

| Problem                    | Solution                                                                                 |
| -------------------------- | ---------------------------------------------------------------------------------------- |
| No closing PR found        | The issue may have been closed manually. These are skipped automatically.                |
| Test passes on buggy code  | The generated test doesn't target the exact bug. Retry or refine the prompt.             |
| Dependency install fails   | Some commits may have incompatible dependency versions. Add pinned install commands.     |
| Rate limited by GitHub API | Add `time.sleep(1)` between API calls, or use authenticated requests (already included). |

---

## Use Cases

- **Research**: study real-world bugs and the effectiveness of AI-generated tests
- **CI/CD hardening**: generate regression tests for past bugs to prevent regressions
- **Benchmarking**: evaluate LLM code understanding by measuring fail-to-pass rates
- **Training data**: build datasets of (bug, test) pairs for fine-tuning models
- **Code review**: understand what kind of bugs a codebase is prone to

---

## API Rate Limits

| Service       | Limit                        | Notes                                |
| ------------- | ---------------------------- | ------------------------------------ |
| GitHub API    | 5,000 req/hr (authenticated) | Pipeline uses ~10 requests per bug   |
| Anthropic API | Varies by plan               | One call per bug for test generation |

---

## License

MIT

---

## Contributing

Contributions are welcome! Some areas that would benefit from improvement:

- Support for more languages and test frameworks (Rust, Java, C++)
- Smarter file truncation — extract only relevant functions instead of full files
- Retry loop that feeds test failures back to Claude for refinement
- Parallel verification with multiple repo clones
- Web UI for browsing results



jq '[.[] | select((.issue_number==1037) or (.issue_number==923) or (.issue_number==780))]' generated_tests/manifest.json > generated_tests/manifest_remaining.json 
python3 verify_test_regenerate.py https://github.com/agentscope-ai/agentscope.git generated_tests/manifest_remaining.json collected_bugs.json