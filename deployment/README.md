# Deployment templates

These files are **templates and runbooks**, not a live deployment. Nothing here
creates billable cloud resources by default. The prototype runs locally; the
production mapping is in [../docs/production_mapping.md](../docs/production_mapping.md).

## Layout

```
deployment/
  vertex_ai/
    README.md              Step-by-step Vertex AI runbook
    Dockerfile             Custom prediction/training image
    requirements.txt
    serve.py               FastAPI prediction service
    pipeline.py            KFP/Vertex pipeline skeleton
    submit_pipeline.py     Compile + submit helper (guarded)
    env.example            Environment variables
  databricks/
    README.md              Databricks runbook
    databricks.yml         Asset Bundle skeleton
    resources/jobs.yml     Workflow definitions
    train_otp.py           Job entrypoint
    score_otp.py           Batch scoring entrypoint
    requirements.txt
```

## Safety

- No credentials are stored here.
- No Skywise or airline endpoints are called.
- The templates assume the repository is packaged as a Python wheel or container image.
- Review IAM, networking, encryption and data contracts before running anything.
