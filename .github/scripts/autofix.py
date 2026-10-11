def failure_log():
    """Read the failed deployment log, including manual runs."""
    global RUN_ID

    if not RUN_ID:
        r = sh([
            "gh", "run", "list",
            "--workflow", "Self-Healing Deploy",
            "--status", "failure",
            "--limit", "1",
            "--json", "databaseId",
            "--jq", ".[0].databaseId"
        ])

        if r.returncode != 0 or not r.stdout.strip():
            print("Could not find a failed Self-Healing Deploy run.")
            print(r.stderr)
            return ""

        RUN_ID = r.stdout.strip()

    r = sh(["gh", "run", "view", RUN_ID, "--log-failed"])

    if r.returncode != 0:
        print("Could not retrieve failure logs for run:", RUN_ID)
        print(r.stderr)
        return ""

    log = r.stdout.strip()
    return log[-MAX_LOG_CHARS:]


def looks_like_a_code_problem(log):
    """Detect a checker failure without guessing from missing logs."""
    if not log:
        print("No deployment failure log was retrieved.")
        return False

    return "FAIL  " + TARGET in log