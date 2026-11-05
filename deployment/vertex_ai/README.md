# Vertex AI deployment runbook

## 1. Prerequisites

- GCP project with billing enabled.
- Vertex AI, Artifact Registry, Cloud Build, Cloud Run and Secret Manager APIs enabled.
- A service account with least-privilege roles for Vertex AI, storage and registry.
- The repository packaged as a wheel (`python -m build`) or a container image.
- Training data staged in `gs://<bucket>/flightops/{env}/raw/` and processed data in
  `gs://<bucket>/flightops/{env}/processed/`.

## 2. Build and push the image

```bash
export PROJECT_ID=your-project
export REGION=europe-west1
export IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/flightops/flightops:0.1.0"

gcloud builds submit --tag "$IMAGE" deployment/vertex_ai
```

The image contains the `flightops` package, the FastAPI service and the training
entrypoints.

## 3. Train locally in the container (smoke test)

```bash
docker run --rm -e GOOGLE_APPLICATION_CREDENTIALS=/tmp/key.json \
  -v "$PWD/data:/app/data" -v "$PWD/artifacts:/app/artifacts" \
  "$IMAGE" python -m flightops.models.otp_model
```

In production, replace the local paths with `gs://` URIs and run the training as a
Vertex AI CustomJob or a pipeline component.

## 4. Compile and submit the pipeline

```bash
python deployment/vertex_ai/submit_pipeline.py \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --pipeline-root "gs://$PROJECT_ID-flightops/pipelines" \
  --image "$IMAGE" \
  --config config.yaml
```

The helper is deliberately guarded: it imports `google.cloud.aiplatform` only when
called and refuses to submit if `--dry-run` is passed. Use `--dry-run` to compile the
pipeline spec without creating resources.

## 5. Register and deploy

1. The pipeline registers the model in Vertex AI Model Registry only if evaluation
   gates pass.
2. Deploy to an endpoint with a custom prediction routine (`serve.py`).
3. Start with 5% canary traffic and a shadow endpoint.
4. Monitor latency, error rate, calibration and drift.
5. Promote to 100% only after sign-off.

## 6. Environment variables

See `env.example`. Never commit real values. Use Secret Manager for credentials.

## 7. Rollback

- Shift endpoint traffic back to the previous model version.
- Keep the last known-good image digest and model artifact immutable.
- Re-run the previous pipeline version if the feature table is corrupted.

## 8. What this template does not do

- It does not create projects, buckets, IAM or VPC-SC.
- It does not include Terraform.
- It does not call Skywise or any airline API.
- It does not guarantee model quality; evaluation gates must be calibrated with the
  airline's model-risk team.
