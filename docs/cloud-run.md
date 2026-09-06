# Deploy the synthetic report to Cloud Run

This recipe deploys the static demonstration server only. It does not provision a model
endpoint, a persistent database, or a production agent service. Deployment requires your
Google Cloud account, billing, enabled build/run APIs, and appropriate IAM permissions.

The default image runs `agent-reliability serve`: a 24-case core report across the refund
and artifact applications. It requires no model credentials or CreatorPal dependency.

From the repository root, after selecting your own project:

```bash
export HARNESS_PROJECT_ID="your-project-id"
gcloud run deploy agent-reliability-harness \
  --project "$HARNESS_PROJECT_ID" \
  --region us-central1 \
  --source . \
  --port 8080 \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 1 \
  --no-allow-unauthenticated
```

The server respects Cloud Run's `PORT` environment variable and binds to `0.0.0.0`. It runs
as a non-root user and writes generated synthetic artifacts under `/tmp`. Every instance
generates its own demo at startup; instance storage is ephemeral. Access remains authenticated
unless you deliberately change the service's access policy. Do not put API keys in the image.

For a locally authenticated preview after deployment:

```bash
gcloud run services proxy agent-reliability-harness \
  --project "$HARNESS_PROJECT_ID" --region us-central1 --port 8080
```

Open `http://localhost:8080`. A public portfolio demo can be enabled separately after reviewing
your project's access and billing settings. No public deployment is needed to run the project
or inspect the report in the repository.

Official reference: [Deploy source code to Cloud Run](https://cloud.google.com/run/docs/deploying-source-code).
