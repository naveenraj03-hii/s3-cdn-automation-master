```python
#!/usr/bin/env python3

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import boto3
from botocore.config import Config

ROOT = Path(__file__).resolve().parents[2]

TARGET = os.getenv("TARGET_FILE", "index.html")
CHECKER = os.getenv("CHECKER", "check-html.py")
BASE_BRANCH = os.getenv("BASE_BRANCH", "main")
DEPLOY_WORKFLOW = os.getenv(
    "DEPLOY_WORKFLOW", "Self-Healing Deploy"
)
RUN_ID = os.getenv("RUN_ID", "").strip()

# AWS Mumbai
REGION = os.getenv("AWS_REGION", "ap-south-1")
MODEL_ID = os.getenv("MODEL_ID", "openai.gpt-oss-120b-1:0")

target_path = (ROOT / TARGET).resolve()
checker_path = (ROOT / CHECKER).resolve()


def cmd(args, check=False):
    result = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(args)}\n{result.stdout}"
        )
    return result


def find_failed_run():
    if RUN_ID:
        return RUN_ID

    print("DEBUG: Looking for the latest failed deployment.")
    result = cmd([
        "gh", "run", "list",
        "--workflow", DEPLOY_WORKFLOW,
        "--status", "failure",
        "--limit", "1",
        "--json", "databaseId",
        "--jq", ".[0].databaseId // empty",
    ])

    run_id = result.stdout.strip()
    if run_id.isdigit():
        print(f"DEBUG: Found deployment run {run_id}")
        return run_id

    print("WARNING: No failed deployment run found.")
    return ""


def get_logs(run_id):
    if not run_id:
        return ""

    result = cmd([
        "gh", "run", "view", run_id, "--log-failed"
    ])
    logs = result.stdout or ""
    print(f"DEBUG: Retrieved logs: {len(logs)} characters")
    return logs


def run_checker(path=None):
    if not checker_path.is_file():
        return 2, f"Checker not found: {checker_path}"

    file_to_check = path or target_path
    if not file_to_check.is_file():
        return 2, f"HTML file not found: {file_to_check}"

    result = cmd([
        sys.executable,
        str(checker_path),
        str(file_to_check),
    ])
    print(result.stdout)
    return result.returncode, result.stdout


def repair_with_bedrock(original_html, checker_output, logs):
    print(f"DEBUG: Calling Bedrock in {REGION}, model {MODEL_ID}")

    client = boto3.client(
        "bedrock-runtime",
        region_name=REGION,
        config=Config(
            connect_timeout=10,
            read_timeout=120,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )

    prompt = f"""
You are an AI HTML repair agent in a CI/CD pipeline.

Fix the supplied HTML so it passes the repository checker.

Rules:
- Return the complete HTML file, not a diff.
- Preserve the existing design, content, CSS and JavaScript.
- Make the smallest change necessary.
- The file must contain exactly one occurrence of SITE-OK.
- The expected marker is <!-- SITE-OK -->.
- Ensure the required HTML skeleton and paired tags are present.
- Do not change AWS infrastructure or GitHub workflow files.
- Return only HTML, without Markdown fences or explanations.

Checker output:
{checker_output}

Deployment log context:
{logs[-4000:]}

Original HTML:
{original_html}
"""

    response = client.converse(
        modelId=MODEL_ID,
        messages=[
            {
                "role": "user",
                "content": [{"text": prompt}],
            }
        ],
        inferenceConfig={
            "maxTokens": 12000,
            "temperature": 0.1,
        },
    )

    parts = response["output"]["message"]["content"]
    text = "".join(p.get("text", "") for p in parts).strip()

    text = re.sub(
        r"\A```(?:html)?\s*|\s*```\Z",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    if "<html" not in text.lower() or "</html>" not in text.lower():
        raise RuntimeError(
            "Bedrock did not return a complete HTML document."
        )

    return text + "\n"


def create_pull_request(run_id, repaired_html):
    if not os.getenv("GH_TOKEN"):
        raise RuntimeError("GH_TOKEN is missing.")

    if not run_id:
        raise RuntimeError(
            "No failed deployment run ID. Run the agent after a failed deployment."
        )

    attempt = os.getenv("GITHUB_RUN_ATTEMPT", "1")
    branch = f"autofix/run-{run_id}-attempt-{attempt}"

    cmd(["git", "fetch", "origin", BASE_BRANCH], check=True)
    cmd([
        "git", "checkout", "-B", branch,
        f"origin/{BASE_BRANCH}",
    ], check=True)

    branch_file = ROOT / TARGET
    branch_file.parent.mkdir(parents=True, exist_ok=True)
    branch_file.write_text(repaired_html, encoding="utf-8")

    # Validate the candidate before committing it.
    code, output = run_checker(branch_file)
    if code != 0:
        raise RuntimeError(
            "Repaired HTML failed validation; no PR created.\n" + output
        )

    cmd(["git", "add", "--", TARGET], check=True)

    diff = cmd(["git", "diff", "--cached", "--quiet"])
    if diff.returncode == 0:
        print("No changes to commit.")
        return

    cmd([
        "git", "config", "user.name", "github-actions[bot]"
    ], check=True)
    cmd([
        "git", "config", "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    ], check=True)
    cmd([
        "git", "commit", "-m",
        f"fix: auto-repair HTML for deployment {run_id}",
    ], check=True)
    cmd(["git", "push", "-u", "origin", branch], check=True)

    existing = cmd([
        "gh", "pr", "list",
        "--head", branch,
        "--state", "open",
        "--json", "url",
        "--jq", ".[0].url // empty",
    ])

    if existing.stdout.strip():
        print("Existing pull request:", existing.stdout.strip())
        return

    result = cmd([
        "gh", "pr", "create",
        "--base", BASE_BRANCH,
        "--head", branch,
        "--title", f"Auto-Fix: repair {TARGET}",
        "--body",
        (
            "Automated HTML repair proposal.\n\n"
            f"- Failed deployment run: {run_id}\n"
            f"- File: `{TARGET}`\n"
            "- Candidate passed the repository HTML checker.\n\n"
            "Review before merging."
        ),
    ])

    if result.returncode != 0:
        raise RuntimeError(
            "Could not create pull request:\n" + result.stdout
        )

    print("Pull request created:", result.stdout.strip())


def main():
    print("=" * 60)
    print("Auto-Fix Agent started")
    print(f"Target: {TARGET}")
    print(f"Checker: {CHECKER}")
    print(f"Region: {REGION}")
    print(f"Model: {MODEL_ID}")
    print("=" * 60)

    run_id = find_failed_run()
    logs = get_logs(run_id)

    # Inspect the actual HTML; don't rely on exact log text.
    code, checker_output = run_checker()

    if code == 0:
        print(
            "HTML validation passed. No repair needed. "
            "If deployment failed, investigate AWS/S3/CloudFront."
        )
        return 0

    if code != 1:
        print("ERROR: Checker could not run; refusing to repair.")
        print(checker_output)
        return 1

    if not target_path.is_file():
        print("ERROR: Target HTML file does not exist.")
        return 1

    original_html = target_path.read_text(encoding="utf-8")
    print("HTML validation failed. Asking Bedrock to repair it.")

    repaired_html = repair_with_bedrock(
        original_html, checker_output, logs
    )

    # Validate the candidate in isolation before touching the PR branch.
    with tempfile.TemporaryDirectory() as temp_dir:
        candidate = Path(temp_dir) / "index.html"
        candidate.write_text(repaired_html, encoding="utf-8")

        code, output = run_checker(candidate)
        if code != 0:
            raise RuntimeError(
                "Bedrock repair failed validation:\n" + output
            )

    create_pull_request(run_id, repaired_html)
    print("Repair validated and submitted for PR review.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERROR: Auto-Fix Agent failed: {exc}")
        sys.exit(1)
```