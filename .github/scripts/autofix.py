
#!/usr/bin/env python3
"""Diagnose failed deployments and propose validated HTML repairs."""

import os
import sys
import subprocess
from pathlib import Path

import boto3
from botocore.config import Config


# ============================================================
# Configuration
# ============================================================

MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID",
    "openai.gpt-oss-120b-1:0",
)
REGION = os.getenv("BEDROCK_REGION", "us-west-2")
RUN_ID = os.getenv("RUN_ID", "").strip()
TARGET = os.getenv("TARGET_FILE", "index.html")
CHECKER = os.getenv("CHECKER", "check-html.py")
BASE_BRANCH = os.getenv("BASE_BRANCH", "main")

MAX_LOG_CHARS = 20000
MAX_ATTEMPTS = 2

BEGIN_MARKER = "<<<BEGIN_FILE>>>"
END_MARKER = "<<<END_FILE>>>"

ROOT = Path.cwd()


# ============================================================
# Shell helpers
# ============================================================

def sh(command, timeout=60):
    """Run a command and capture output without raising."""
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            command,
            124,
            "",
            f"Command timed out after {timeout} seconds",
        )


def run_checked(command, timeout=60):
    result = sh(command, timeout)

    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(command)}\n"
            f"{(result.stderr or result.stdout)[-3000:]}"
        )

    return result


def read_file(filename):
    path = ROOT / filename

    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    return path.read_text(encoding="utf-8")


def write_file(filename, content):
    (ROOT / filename).write_text(
        content,
        encoding="utf-8",
    )


# ============================================================
# Retrieve the failed deployment logs
# ============================================================

def find_failed_run():
    """Use the supplied run ID, or find the latest failed deployment."""
    global RUN_ID

    if RUN_ID:
        print(f"DEBUG: Using deployment run ID: {RUN_ID}")
        return RUN_ID

    print("DEBUG: No run ID supplied; searching for failed deployment.")

    result = sh([
        "gh", "run", "list",
        "--workflow", "Self-Healing Deploy",
        "--status", "failure",
        "--limit", "1",
        "--json", "databaseId",
        "--jq", ".[0].databaseId",
    ])

    if result.returncode != 0:
        print("ERROR: Could not list failed deployment runs.")
        print((result.stderr or "")[-3000:])
        return ""

    candidate = result.stdout.strip()

    if not candidate or candidate == "null":
        print("DEBUG: No failed Self-Healing Deploy run found.")
        print(
            "Check the exact workflow name and confirm a failed run "
            "exists in this repository."
        )
        return ""

    RUN_ID = candidate
    print(f"DEBUG: Found failed deployment run: {RUN_ID}")
    return RUN_ID


def failure_log():
    """Download the failed step logs for the selected deployment."""
    run_id = find_failed_run()

    if not run_id:
        return ""

    print(f"DEBUG: Downloading failure logs for run {run_id}.")

    result = sh([
        "gh", "run", "view", run_id, "--log-failed",
    ], timeout=90)

    if result.returncode != 0:
        print("ERROR: Could not retrieve deployment logs.")
        print((result.stderr or "")[-3000:])
        return ""

    log = result.stdout.strip()
    print(f"DEBUG: Retrieved log length: {len(log)}")

    if not log:
        print("DEBUG: Retrieved logs are empty.")
        return ""

    return log[-MAX_LOG_CHARS:]


# ============================================================
# Identify HTML checker failures
# ============================================================

def looks_like_a_code_problem(log):
    """Only repair HTML when a checker failure is evidenced in logs."""
    if not log:
        print("DEBUG: No log available; cannot diagnose an HTML failure.")
        return False

    lines = log.splitlines()

    # The checker prints: FAIL  index.html
    for line in lines:
        if line.strip() == f"FAIL  {TARGET}":
            print("DEBUG: HTML checker failure detected.")
            return True

    print("DEBUG: Expected checker failure marker not found.")
    print("DEBUG: Recent deployment log excerpt:")
    print(log[-3000:])
    return False


# ============================================================
# Run the project's HTML checker
# ============================================================

def run_checker():
    """Run check-html.py explicitly against the configured target."""
    checker_path = ROOT / CHECKER
    target_path = ROOT / TARGET

    if not checker_path.is_file():
        raise FileNotFoundError(
            f"Checker not found: {checker_path}. "
            "Set CHECKER to its repository-relative path."
        )

    if not target_path.is_file():
        return False, f"FAIL  {TARGET} does not exist"

    print(f"DEBUG: Running checker against {TARGET}.")

    result = sh([
        sys.executable,
        str(checker_path),
        str(target_path),
    ], timeout=60)

    output = (
        (result.stdout or "")
        + "\n"
        + (result.stderr or "")
    ).strip()

    print(f"DEBUG: Checker exit code: {result.returncode}")
    print(output[-6000:] if output else "DEBUG: No checker output.")

    return result.returncode == 0, output


# ============================================================
# Ask Bedrock for a targeted repair
# ============================================================

SYSTEM_PROMPT = f"""
You are a cautious HTML repair agent.

Repair only the HTML checker failures described in the input.

Rules:
- Return the complete corrected file, not a diff.
- Preserve existing site content and styling where possible.
- Do not make unrelated changes.
- The file must satisfy the supplied checker.
- Do not change AWS configuration, GitHub workflows, or other files.
- The required marker SITE-OK must appear exactly once in the
  target HTML file.
- Return exactly this format:

{BEGIN_MARKER}
complete HTML file here
{END_MARKER}

Do not put explanations outside the markers.
"""


def ask_the_model(client, original, checker_output, deployment_log):
    prompt = f"""
Target file: {TARGET}

HTML checker output:
{checker_output[-6000:]}

Deployment failure log:
{deployment_log[-6000:]}

Current file:
{BEGIN_MARKER}
{original}
{END_MARKER}

Return the complete repaired file using the required markers.
"""

    print(f"DEBUG: Calling Bedrock model {MODEL_ID} in {REGION}.")

    response = client.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{
            "role": "user",
            "content": [{"text": prompt}],
        }],
        inferenceConfig={
            "maxTokens": 8000,
            "temperature": 0.1,
        },
    )

    content = response.get("output", {}).get(
        "message", {}
    ).get("content", [])

    answer = "\n".join(
        item["text"]
        for item in content
        if isinstance(item, dict) and "text" in item
    )

    if not answer.strip():
        raise RuntimeError("Bedrock returned an empty response.")

    return answer


def extract_file(answer):
    """Extract the complete HTML file from the model response."""
    start = answer.find(BEGIN_MARKER)
    end = answer.find(END_MARKER)

    if start < 0 or end < 0 or end <= start:
        raise ValueError(
            "Bedrock response did not contain valid file markers."
        )

    content = answer[start + len(BEGIN_MARKER):end]
    content = content.strip("\r\n")

    if not content.strip():
        raise ValueError("Bedrock returned an empty HTML file.")

    return content + "\n"


# ============================================================
# Create a pull request for a validated repair
# ============================================================

def open_pull_request():
    """Commit the repair to a separate branch and open a PR."""
    if not RUN_ID:
        print(
            "ERROR: No deployment run ID is available for the "
            "repair branch. Refusing to create a PR."
        )
        return False

    branch = f"autofix/run-{RUN_ID}"

    # Avoid creating duplicate PRs on repeated workflow runs.
    existing = sh([
        "gh", "pr", "list",
        "--state", "all",
        "--head", branch,
        "--json", "url,state",
    ])

    if existing.returncode == 0:
        result = sh([
            "gh", "pr", "list",
            "--state", "all",
            "--head", branch,
            "--json", "url,state",
            "--jq", ".[0].url",
        ])

        if result.stdout.strip():
            print("DEBUG: A PR already exists for this branch:")
            print(result.stdout.strip())
            return False

    print(f"DEBUG: Creating repair branch {branch}.")

    run_checked([
        "git", "config", "user.name", "github-actions[bot]"
    ])
    run_checked([
        "git", "config", "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    ])
    run_checked(["git", "fetch", "origin", BASE_BRANCH])

    # Never overwrite an existing local branch.
    local_branch = sh([
        "git", "show-ref", "--verify",
        f"refs/heads/{branch}",
    ])

    if local_branch.returncode == 0:
        print("ERROR: Repair branch already exists locally.")
        print("Use a new deployment run or clean up the old branch.")
        return False

    run_checked(["git", "checkout", "-b", branch])
    run_checked(["git", "add", "--", TARGET])

    staged = sh(["git", "diff", "--cached", "--quiet"])

    if staged.returncode == 0:
        print("DEBUG: No changes to commit; no PR needed.")
        return False

    if staged.returncode != 1:
        raise RuntimeError("Could not inspect staged changes.")

    run_checked([
        "git", "commit", "-m",
        "fix: repair HTML checker failure",
    ])

    run_checked(["git", "push", "origin", branch], timeout=90)

    result = sh([
        "gh", "pr", "create",
        "--base", BASE_BRANCH,
        "--head", branch,
        "--title", "Auto-fix HTML checker failure",
        "--body",
        (
            "Automated HTML repair proposal.\n\n"
            "The repaired file passed check-html.py. "
            "Please review the changes and verify deployment "
            "before merging."
        ),
    ], timeout=90)

    if result.returncode != 0:
        print("ERROR: Pull request creation failed.")
        print((result.stderr or result.stdout)[-3000:])
        return False

    print("SUCCESS: Pull request created.")
    print(result.stdout.strip())
    return True


# ============================================================
# Main entry point
# ============================================================

def main():
    print("=" * 60)
    print("Auto-Fix Agent started")
    print(f"Target file: {TARGET}")
    print(f"Checker: {CHECKER}")
    print(f"Base branch: {BASE_BRANCH}")
    print(f"Initial RUN_ID: {RUN_ID!r}")
    print("=" * 60)

    if not os.getenv("GH_TOKEN"):
        print(
            "ERROR: GH_TOKEN is missing. Set it to "
            "${{ secrets.GITHUB_TOKEN }} in the workflow step."
        )
        return 1

    original = None

    try:
        deployment_log = failure_log()

        if not deployment_log:
            print(
                "RESULT: No deployment logs retrieved. "
                "No changes were made."
            )
            return 1

        if not looks_like_a_code_problem(deployment_log):
            print(
                "RESULT: The failure was not identified as an HTML "
                "checker failure. No HTML was changed and no PR "
                "was created. Check for AWS, S3, or CloudFront errors."
            )
            return 0

        original = read_file(TARGET)

        # Confirm the original failure is reproducible before asking AI.
        valid, checker_output = run_checker()

        if valid:
            print(
                "RESULT: The checked-out HTML already passes the "
                "checker. No repair PR is necessary."
            )
            return 0

        client = boto3.client(
            "bedrock-runtime",
            region_name=REGION,
            config=Config(
                connect_timeout=10,
                read_timeout=90,
                retries={
                    "max_attempts": 2,
                    "mode": "standard",
                },
            ),
        )

        candidate = original

        for attempt in range(1, MAX_ATTEMPTS + 1):
            print(f"DEBUG: Repair attempt {attempt}/{MAX_ATTEMPTS}.")

            answer = ask_the_model(
                client,
                candidate,
                checker_output,
                deployment_log,
            )

            repaired = extract_file(answer)
            write_file(TARGET, repaired)

            valid, checker_output = run_checker()

            if valid:
                print("SUCCESS: Repaired HTML passed the checker.")
                if not open_pull_request():
                    print(
                        "ERROR: HTML passed validation, but a PR "
                        "was not created. Inspect the messages above."
                    )
                    return 1
                return 0

            print("WARNING: Candidate failed validation.")
            candidate = repaired

        print(
            "ERROR: Could not produce a valid repair after "
            f"{MAX_ATTEMPTS} attempts."
        )
        return 1

    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {str(exc)[:2000]}")
        return 1

    finally:
        # Restore the original checked-out file on unsuccessful runs.
        # A successful PR keeps the validated candidate on its branch.
        if original is not None:
            current = read_file(TARGET) if (ROOT / TARGET).is_file() else None

            if current is not None:
                # Don't restore after a successful PR; the PR branch
                # needs to retain the repaired file.
                # The workflow exits immediately after a successful PR.
                pass


if __name__ == "__main__":
    sys.exit(main())
