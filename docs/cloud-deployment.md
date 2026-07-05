# Cloud Deployment

This guide describes the planned production shape for the EDINET watcher.

The local default remains SQLite plus local files. Cloud Run should use
Firestore for workflow state so hourly jobs do not forget what they have
already processed.

## Target Architecture

- GitHub Actions runs tests.
- GitHub Actions submits `cloudbuild.yaml`.
- Cloud Build builds and pushes the Docker image to Artifact Registry.
- Cloud Build deploys or updates a Cloud Run Job.
- Cloud Scheduler executes the Cloud Run Job during Tokyo working hours.
- Firestore stores filings, drafts, and follow-up schedules.
- Firebase Hosting serves the generated static site.

## One-Time Google Cloud Setup

Use the existing Firebase/Google Cloud project:

```text
activists-edinet
```

Enable APIs:

```bash
gcloud services enable \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com \
  firestore.googleapis.com \
  firebasehosting.googleapis.com \
  iamcredentials.googleapis.com \
  --project activists-edinet
```

Create an Artifact Registry repository:

```bash
gcloud artifacts repositories create edinet-watcher \
  --repository-format=docker \
  --location=asia-northeast1 \
  --project=activists-edinet
```

Create service accounts:

```bash
gcloud iam service-accounts create edinet-watcher-runner \
  --display-name="EDINET watcher Cloud Run job" \
  --project=activists-edinet

gcloud iam service-accounts create edinet-watcher-deployer \
  --display-name="EDINET watcher GitHub deployer" \
  --project=activists-edinet
```

Grant the runner access to Firestore:

```bash
gcloud projects add-iam-policy-binding activists-edinet \
  --member="serviceAccount:edinet-watcher-runner@activists-edinet.iam.gserviceaccount.com" \
  --role="roles/datastore.user"
```

Secret Manager access should be granted per secret, after the secrets are
created:

```bash
for secret in edinet-api-key openai-api-key smtp-host smtp-user smtp-password email-from email-to; do
  gcloud secrets add-iam-policy-binding "$secret" \
    --member="serviceAccount:edinet-watcher-runner@activists-edinet.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor" \
    --project=activists-edinet
done
```

Firebase Hosting deployment from Cloud Run still needs a deploy permission. The
narrowest practical permission may depend on whether Firebase CLI accepts the
Cloud Run service account. If it does, grant:

```bash
gcloud projects add-iam-policy-binding activists-edinet \
  --member="serviceAccount:edinet-watcher-runner@activists-edinet.iam.gserviceaccount.com" \
  --role="roles/firebasehosting.admin"
```

Grant the Cloud Build service account permission to push images to the one
Artifact Registry repository:

```bash
gcloud artifacts repositories add-iam-policy-binding edinet-watcher \
  --location=asia-northeast1 \
  --member="serviceAccount:1073110031446@cloudbuild.gserviceaccount.com" \
  --role="roles/artifactregistry.writer"
```

To let Cloud Build update the Cloud Run Job, grant these broader permissions
only after you are ready to deploy:

```bash
gcloud projects add-iam-policy-binding activists-edinet \
  --member="serviceAccount:1073110031446@cloudbuild.gserviceaccount.com" \
  --role="roles/run.developer"

gcloud iam service-accounts add-iam-policy-binding \
  edinet-watcher-runner@activists-edinet.iam.gserviceaccount.com \
  --member="serviceAccount:1073110031446@cloudbuild.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser" \
  --project=activists-edinet
```

To let GitHub submit Cloud Builds through the deployer account, create a
Workload Identity pool/provider restricted to this repository:

```bash
gcloud iam workload-identity-pools create github \
  --location=global \
  --display-name=GitHub-Actions \
  --project=activists-edinet

gcloud iam workload-identity-pools providers create-oidc github \
  --location=global \
  --workload-identity-pool=github \
  --display-name=GitHub-Actions \
  --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping=google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository,attribute.ref=assertion.ref \
  --attribute-condition="assertion.repository=='Kujiranoai/edinet-activists'" \
  --project=activists-edinet

gcloud iam service-accounts add-iam-policy-binding \
  edinet-watcher-deployer@activists-edinet.iam.gserviceaccount.com \
  --member=principalSet://iam.googleapis.com/projects/1073110031446/locations/global/workloadIdentityPools/github/attribute.repository/Kujiranoai/edinet-activists \
  --role=roles/iam.workloadIdentityUser \
  --project=activists-edinet
```

Then grant the deployer account permission to submit builds:

```bash
gcloud projects add-iam-policy-binding activists-edinet \
  --member="serviceAccount:edinet-watcher-deployer@activists-edinet.iam.gserviceaccount.com" \
  --role="roles/cloudbuild.builds.editor"
```

If the deployer submits builds that use a non-default service account, it may
also need:

```bash
gcloud projects add-iam-policy-binding activists-edinet \
  --member="serviceAccount:edinet-watcher-deployer@activists-edinet.iam.gserviceaccount.com" \
  --role="roles/run.developer"

gcloud iam service-accounts add-iam-policy-binding \
  edinet-watcher-runner@activists-edinet.iam.gserviceaccount.com \
  --member="serviceAccount:edinet-watcher-deployer@activists-edinet.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser" \
  --project=activists-edinet
```

Create Secret Manager secrets expected by `cloudbuild.yaml`:

```bash
gcloud secrets create edinet-api-key --replication-policy=automatic --project=activists-edinet
gcloud secrets create openai-api-key --replication-policy=automatic --project=activists-edinet
gcloud secrets create smtp-host --replication-policy=automatic --project=activists-edinet
gcloud secrets create smtp-user --replication-policy=automatic --project=activists-edinet
gcloud secrets create smtp-password --replication-policy=automatic --project=activists-edinet
gcloud secrets create email-from --replication-policy=automatic --project=activists-edinet
gcloud secrets create email-to --replication-policy=automatic --project=activists-edinet
```

Add values:

```bash
printf '%s' 'YOUR_VALUE' | gcloud secrets versions add openai-api-key --data-file=- --project=activists-edinet
```

Repeat for each secret.

## GitHub Setup

Add a repository variable:

```text
GCP_PROJECT_ID=activists-edinet
```

Add repository secrets:

```text
GCP_WORKLOAD_IDENTITY_PROVIDER=projects/1073110031446/locations/global/workloadIdentityPools/github/providers/github
GCP_DEPLOY_SERVICE_ACCOUNT=edinet-watcher-deployer@activists-edinet.iam.gserviceaccount.com
```

The Workload Identity provider has to be created in Google Cloud and linked to
your GitHub repository. After that, the `Deploy Cloud Run Job` workflow can
submit Cloud Build without storing a JSON service-account key in GitHub.

## First Cloud Run

After the GitHub deploy workflow succeeds, run the Cloud Run Job manually for a
one-month backfill by temporarily setting `_SCAN_DAYS` to `31` in the workflow
input, or by executing:

```bash
gcloud run jobs execute edinet-watcher-hourly \
  --region=asia-northeast1 \
  --project=activists-edinet \
  --wait
```

Then inspect logs:

```bash
gcloud run jobs executions list \
  --job=edinet-watcher-hourly \
  --region=asia-northeast1 \
  --project=activists-edinet
```

## Scheduler

Create the hourly working-hours schedule after the manual run works:

```bash
gcloud scheduler jobs create http edinet-watcher-hourly \
  --location=asia-northeast1 \
  --schedule="0 9-17 * * 1-5" \
  --time-zone="Asia/Tokyo" \
  --uri="https://asia-northeast1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/activists-edinet/jobs/edinet-watcher-hourly:run" \
  --http-method=POST \
  --oauth-service-account-email="edinet-watcher-runner@activists-edinet.iam.gserviceaccount.com" \
  --project=activists-edinet
```

The same pattern can create a daily follow-up job once a separate Cloud Run Job
is deployed with args:

```text
followups run
```

## Current Caveat

The container includes Firebase CLI so `publish --deploy` can be tested from
Cloud Run. If Firebase CLI authentication from the Cloud Run service account is
not accepted in your project, the next implementation step is to replace that
deploy call with Firebase Hosting REST API deployment.
