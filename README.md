# AI-Powered Self-Healing Deployment on AWS

Automate static website deployment with **Amazon S3**, **Amazon CloudFront**, and **GitHub Actions**, with an AI-assisted workflow designed to analyze deployment failures and propose fixes.

> **Project status:** Update this section to match your latest test results. The standard deployment pipeline and the AI auto-fix workflow should be tested independently before describing every feature as production-ready.

## Overview

This project demonstrates a CI/CD workflow for publishing a static website to Amazon S3 and distributing it through Amazon CloudFront. GitHub Actions runs the deployment workflow when changes are pushed to the configured branch. A Python validation script checks the HTML before deployment. An additional workflow is intended to analyze eligible failures with Amazon Bedrock and help propose a correction through a GitHub pull request.

## Architecture

```text
Developer pushes code to GitHub
              |
              v
     GitHub Actions workflow
              |
              v
      Python HTML validation
              |
        +-----+------+
        |            |
      Pass          Fail
        |            |
        v            v
 Upload files     Failure analysis
 to Amazon S3     (AI workflow, if
        |          configured)
        v            |
 Amazon CloudFront   v
        |        Proposed fix / PR
        v
  Static website
```

The exact failure-handling and pull-request behavior depends on the current workflow and Python script configuration.

## Features

- **Automated deployment:** Use GitHub Actions to publish website files to Amazon S3.
- **Content delivery:** Serve the static site through Amazon CloudFront.
- **HTML validation:** Run a Python checker before publishing the site.
- **AI-assisted troubleshooting:** Integrate Amazon Bedrock to analyze eligible workflow failures, if configured and tested.
- **Pull-request workflow:** Allow the automation to propose a code fix for review, if repository permissions and the auto-fix script support it.
- **CloudFront cache invalidation:** Refresh cached content after a successful deployment, if enabled in the deployment workflow.

## Technology stack

- Amazon S3
- Amazon CloudFront
- AWS IAM
- Amazon Bedrock (AI-assisted failure analysis)
- GitHub Actions
- Python
- Git and GitHub

## Repository structure

```text
.
├── .github/
│   ├── workflows/
│   │   ├── deploy-s3.yml       # Deployment workflow
│   │   └── autofix.yml         # AI auto-fix workflow
│   └── scripts/
│       └── autofix.py          # AI failure analysis / fix logic
├── index.html                  # Static website
└── check-html.py               # HTML validation script
```

> Adjust this tree if your repository uses different filenames or directories.

## How deployment works

1. Make a change to the website, such as `index.html`.
2. Commit and push the change to the configured branch (currently `main`, if that is how your workflow is configured).
3. GitHub Actions starts the deployment workflow.
4. The validation script checks that the HTML contains the required document structure and exactly one `SITE-OK` marker.
5. If validation succeeds, the workflow uploads the website files to Amazon S3.
6. If configured, the workflow creates a CloudFront invalidation so visitors receive the updated content.
7. If the workflow fails, the separate auto-fix workflow may inspect the failure. It should only attempt code repairs for failure types it explicitly supports.

## HTML validation

The `check-html.py` script performs simple checks on `index.html`, including:

- The file exists and is not empty.
- The `SITE-OK` marker appears exactly once.
- Required HTML document elements are present.
- Selected opening and closing tags have matching counts.

This is a lightweight project-specific checker, not a full HTML standards validator. Passing it does not guarantee that every browser will render the page correctly.

Run the checker locally:

```bash
python check-html.py index.html
```

A successful check should print an `OK` message. A failed check prints the detected problems and exits with a non-zero status.

## AI auto-fix workflow

The optional AI workflow is intended to:

1. Inspect a failed GitHub Actions deployment run.
2. Retrieve relevant failure logs.
3. Determine whether the failure is one the agent is designed to handle.
4. Use Amazon Bedrock for AI-assisted analysis, if access and model configuration are valid.
5. Propose a code change and open a pull request, if the script implements this behavior and GitHub permissions allow it.

**Important limitations**

- An AWS/S3/CloudFront permission error is not the same as an HTML validation error. The agent should not change website code to “fix” an infrastructure permissions issue.
- AI output should be reviewed before merging.
- The model ID must be available to your AWS account in the configured region, and the IAM identity must be allowed to invoke it.
- Pull-request creation requires appropriate workflow permissions and repository settings.
- Verify the actual workflow logs and pull request before claiming the auto-fix path is working end to end.

## Prerequisites

- A GitHub repository with Actions enabled.
- An AWS account.
- An S3 bucket for the static site.
- A CloudFront distribution configured to serve the bucket.
- An IAM identity with only the permissions required by the deployment and AI workflows.
- Python 3 for local validation.
- For the AI feature: Amazon Bedrock model access in the selected region and permission to invoke that model.

## Configuration

Update the workflow files to match your AWS resources and repository.

Typical deployment settings include:

- AWS region: `ap-south-1` (Mumbai), if that is where your S3 bucket is located.
- S3 bucket name: your bucket name.
- CloudFront distribution ID: your distribution ID.
- Deployment branch: `main`, if that is your intended branch.

For GitHub Actions, add the required secrets under:

**GitHub repository → Settings → Secrets and variables → Actions**

Common secret names used by this project:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

Use an IAM identity with least-privilege permissions. Never commit AWS credentials, access keys, tokens, or other secrets to the repository. Consider using GitHub OIDC with an AWS IAM role instead of long-lived access keys.

For the AI workflow, configure the Bedrock model ID and region consistently between the workflow and Python script. Do not assume a model is available in a region until you confirm it in the Amazon Bedrock console.

## GitHub Actions permissions

If the auto-fix script opens pull requests, confirm that:

- The workflow grants the required `contents: write` and `pull-requests: write` permissions.
- The repository allows GitHub Actions to create pull requests.
- The script uses the supplied GitHub token correctly.
- Branch protection rules and organization policies permit the intended action.

Grant only the permissions the workflow actually needs.

## Testing the workflow

Test in a controlled branch or test repository first.

1. **Valid HTML:** Push a valid page and confirm validation passes.
2. **Validation failure:** Make a deliberate, reversible change that violates a checker rule (for example, change the required `SITE-OK` marker), then push it.
3. **Agent diagnosis:** Confirm the auto-fix workflow runs and correctly identifies the validation failure.
4. **Pull request:** If supported, verify that a proposed fix is opened as a pull request and inspect the diff before merging.
5. **Successful deployment:** After the fix is merged, confirm the deployment succeeds and the CloudFront site displays the expected page.

A failed deployment caused by AWS permissions, missing backup data, or CloudFront/S3 configuration should be diagnosed separately rather than treated as an HTML validation failure.

## Project links

- **GitHub repository:** https://github.com/naveenraj03-hii/s3-cdn-automation-master
- **CloudFront website:** https://d1kriohsns2kmu.cloudfront.net/

Confirm both links are accessible before sharing this README publicly. A private S3 bucket may correctly deny direct public access when CloudFront is the intended entry point.

## Security notes

- Do not store AWS keys in source code or commit them to Git.
- Prefer short-lived credentials through GitHub OIDC where practical.
- Keep IAM policies scoped to the specific resources and actions required.
- Review AI-generated changes before merging.
- Avoid printing secrets or sensitive environment values in workflow logs.

## Learning outcomes

This project is intended to demonstrate practical learning in:

- Cloud hosting for static websites
- CI/CD automation with GitHub Actions
- S3 and CloudFront integration
- Python-based validation
- AWS IAM and secret management
- AI-assisted DevOps troubleshooting

## Future improvements

- Use GitHub OIDC instead of long-lived AWS access keys.
- Add comprehensive HTML validation and automated tests.
- Add clearer failure classification for application versus infrastructure errors.
- Add unit tests for the auto-fix script.
- Record the fix proposal and test results in pull requests.
- Add monitoring, alerts, and deployment status reporting.

## License

Choose and add a license file if you want others to reuse or distribute this project. Until then, no license is specified.
