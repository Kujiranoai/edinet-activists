# Cloud Deployment Post-Mortem

Date: 2026-07-05

This note records the main deployment problems encountered while setting up the
GitHub Actions -> Cloud Build -> Cloud Run Job pipeline for the EDINET watcher.
The goal is not blame. The goal is to make the next deployment calmer, faster,
and less dependent on remembering a chain of Google Cloud IAM details.

## Intended Deployment Flow

The target flow was:

1. GitHub Actions authenticates to Google Cloud using Workload Identity
   Federation.
2. GitHub Actions submits `cloudbuild.yaml`.
3. Cloud Build uploads source, builds the Docker image, and pushes it to
   Artifact Registry.
4. Cloud Build deploys or updates the Cloud Run Job.
5. The Cloud Run Job later runs the EDINET watcher using Firestore, Secret
   Manager, and Firebase Hosting.

This means there are three important identities, not one:

- GitHub's federated deployer service account:
  `edinet-watcher-deployer@activists-edinet.iam.gserviceaccount.com`
- Cloud Build's execution identity:
  `1073110031446-compute@developer.gserviceaccount.com`
- The Cloud Run runtime service account:
  `edinet-watcher-runner@activists-edinet.iam.gserviceaccount.com`

Most of the problems came from one identity needing to hand work to the next
identity without having the exact permission required for that handoff.

## Issues Encountered

### 1. GitHub OAuth Token Could Not Push Workflow Files

The first push failed because GitHub rejected changes to:

```text
.github/workflows/deploy-cloud-run-job.yml
```

GitHub requires the authenticated token to have the `workflow` scope before it
can create or update workflow files. A normal repo push scope is not enough.

Fix:

- Refresh GitHub CLI authentication with workflow permission.
- Push again after the token included `workflow` scope.

Avoid next time:

- Before editing workflow files, run `gh auth status`.
- If workflow edits are expected, ensure the active GitHub token has `workflow`
  scope before starting the deployment work.

### 2. Deployer Could Not Use Cloud Build Source Staging Bucket

The GitHub deployer was able to authenticate to Google Cloud, but
`gcloud builds submit` failed when it tried to upload source to the Cloud Build
staging bucket.

There were two related concepts:

- The deployer needs object permissions to upload the source archive.
- It may also need bucket metadata/read permission, not just object write
  permission.

Fix:

- Created a dedicated staging bucket:
  `gs://activists-edinet-cloudbuild-source`
- Granted the deployer `roles/storage.objectAdmin`.
- Granted the deployer `roles/storage.legacyBucketReader`.
- Granted Cloud Build read access to the staged source object.

Avoid next time:

- Prefer a dedicated Cloud Build source bucket instead of relying on the default
  Cloud Build bucket.
- Grant both object write and bucket-read style access to the deployer.
- Keep the bucket project-specific and deployment-specific, so permissions are
  easy to reason about.

### 3. Deployer Could Not Act As Cloud Build's Execution Service Account

After source upload succeeded, Cloud Build submission failed because the GitHub
deployer could not act as the service account Cloud Build was going to use.

The confusing part was that the error referenced a numeric service account ID:

```text
107381375849082257979
```

That mapped to:

```text
1073110031446-compute@developer.gserviceaccount.com
```

Fix:

- Identified the service account using:
  `gcloud iam service-accounts list`.
- Granted the GitHub deployer `roles/iam.serviceAccountUser` on the default
  compute service account.

Avoid next time:

- When an error references a service account numeric ID, map it back with:
  `gcloud iam service-accounts list --format='table(email,uniqueId)'`.
- Remember that submitting a build can require `iam.serviceAccounts.actAs` on
  the build execution identity.

### 4. Empty Image Tag From Cloud Build Built-In Substitution

The Docker image tag was configured using `${SHORT_SHA}`. In this deployment
path, `SHORT_SHA` was empty, causing an invalid image name ending in a colon.

The failed image reference looked like:

```text
asia-northeast1-docker.pkg.dev/activists-edinet/edinet-watcher/edinet-watcher:
```

Fix:

- Added an explicit `_IMAGE_TAG` Cloud Build substitution.
- Passed `${{ github.sha }}` from GitHub Actions.
- Used `${_IMAGE_TAG}` consistently in `cloudbuild.yaml`.

Avoid next time:

- Do not assume all Cloud Build built-in substitutions are populated for all
  submission modes.
- For GitHub-triggered builds, pass the image tag explicitly from GitHub.
- Use a safe default in `cloudbuild.yaml` for local/manual runs.

### 5. Nested Cloud Build Substitution Did Not Expand As Expected

The Cloud Run runner service account default was initially written as:

```yaml
_SERVICE_ACCOUNT: edinet-watcher-runner@${PROJECT_ID}.iam.gserviceaccount.com
```

Cloud Build did not expand `${PROJECT_ID}` inside that custom substitution in
the way expected. The deploy command received a literal string containing
`${PROJECT_ID}`.

Fix:

- Passed `_SERVICE_ACCOUNT` explicitly from GitHub Actions.
- Made the default in `cloudbuild.yaml` explicit:
  `edinet-watcher-runner@activists-edinet.iam.gserviceaccount.com`.

Avoid next time:

- Avoid nested substitutions inside custom Cloud Build substitutions.
- Pass important identity values explicitly.
- Prefer clarity over clever interpolation for IAM-sensitive values.

### 6. Cloud Build Could Not Deploy As The Cloud Run Runtime Service Account

Cloud Build successfully built and pushed the image, but deployment failed when
it tried to create/update the Cloud Run Job using the runner service account.

The key permission was:

```text
iam.serviceAccounts.actAs
```

Fix:

- Granted the Cloud Build execution identity
  `roles/iam.serviceAccountUser` on:
  `edinet-watcher-runner@activists-edinet.iam.gserviceaccount.com`.

Avoid next time:

- Separate these two permissions mentally:
  - permission to deploy Cloud Run resources, such as `roles/run.developer`
  - permission to attach a runtime service account, namely
    `roles/iam.serviceAccountUser` on that service account
- A Cloud Run deployment often needs both.

### 7. Cloud Build Succeeded But GitHub Actions Still Failed

One run successfully deployed the Cloud Run Job, but GitHub Actions reported the
`gcloud builds submit` step as failed. The reason was not the build itself. The
GitHub deployer lacked permission to stream/read the Cloud Build logs at the end
of the command.

Fix:

- Granted the GitHub deployer `roles/viewer` on the project.
- Re-ran the workflow and confirmed GitHub Actions completed green.

Avoid next time:

- Verify the Cloud Build resource directly before assuming GitHub's wrapper
  failure means deployment failed:
  `gcloud builds describe BUILD_ID`.
- Ensure the service account running `gcloud builds submit` can read the build
  status/log metadata it is expected to stream.

## Final Working State

The final deployment workflow completed successfully:

```text
GitHub Actions run: 28741948608
Cloud Run Job: edinet-watcher-hourly
Region: asia-northeast1
Project: activists-edinet
Runtime service account: edinet-watcher-runner@activists-edinet.iam.gserviceaccount.com
```

The deployed job is configured with:

- Firestore backend
- Secret Manager-backed EDINET, OpenAI, SMTP, and email settings
- Firebase project/site settings
- command arguments:
  `run --days 3 --publish --deploy`

The job was deployed, but it was not executed during this setup. Executing it is
a separate operational action because it can send email and deploy website
changes.

## Lessons Learned

### Model The Identity Chain First

Before building the workflow, list each identity and what it must do:

- GitHub deployer:
  authenticate, upload source, submit builds, read build status/logs
- Cloud Build execution account:
  read source, build image, push image, deploy Cloud Run Job, act as runner
- Cloud Run runner:
  read secrets, read/write Firestore, deploy Firebase Hosting, call external APIs

This prevents treating "Google Cloud permissions" as one large bucket.

### Test The Pipeline In Layers

A good order is:

1. `gh auth status`
2. Workload Identity authentication in GitHub
3. `gcloud builds submit` source upload
4. Docker build
5. Artifact Registry push
6. Cloud Run Job deploy
7. Cloud Run Job execute
8. Cloud Scheduler automation

When a layer fails, fix that layer and rerun. Do not jump ahead to runtime
testing until deployment is green.

### Make Build Inputs Explicit

The deployment became more reliable once the workflow explicitly passed:

- `_IMAGE_TAG`
- `_SERVICE_ACCOUNT`
- `_REGION`
- `_SCAN_DAYS`

This is less magical and easier to debug than relying on implicit Cloud Build
state.

### Treat A Green Cloud Build And A Green GitHub Run As Different Checks

Cloud Build can succeed while GitHub Actions fails because GitHub is merely the
client submitting and polling the build. Always check both:

```bash
gh run view RUN_ID
gcloud builds describe BUILD_ID --project=activists-edinet --region=global
```

### Keep The First Runtime Execution Manual

The deployed Cloud Run Job uses live credentials and `--publish --deploy`.
Running it can send email and update Firebase Hosting. That is correct for
production, but the first execution should be intentional and observed.

## Recommended Future Improvements

- Add a preflight script that checks required IAM bindings before running the
  deployment workflow.
- Consider using a dedicated Cloud Build service account rather than the default
  compute service account, so permissions are narrower and clearer.
- Consider using a custom Cloud Build logs bucket to avoid log-streaming
  permission surprises.
- Reduce Docker build time by using a slimmer Firebase deployment strategy or a
  base image that already contains Node/Firebase CLI dependencies.
- Add a separate "deploy dry run" workflow input that deploys the job without
  immediately changing the runtime command intended for production.
- Add a documented Cloud Scheduler setup after the first manual Cloud Run
  execution is proven.

