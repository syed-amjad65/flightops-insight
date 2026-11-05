"""Compile or submit the Vertex AI pipeline.

Safe by default: without ``--submit`` it only compiles the pipeline spec. Submission
requires explicit credentials and a project. No resources are created by the compile
step.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="your-project")
    parser.add_argument("--region", default="europe-west1")
    parser.add_argument("--pipeline-root", default="gs://your-bucket/flightops/pipelines")
    parser.add_argument("--image", required=True, help="Container image with the flightops package")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output", default="deployment/vertex_ai/flightops_pipeline.yaml")
    parser.add_argument("--submit", action="store_true", help="Actually submit to Vertex AI")
    args = parser.parse_args()

    from deployment.vertex_ai.pipeline import compile_pipeline

    spec = compile_pipeline(args.output, args.image)
    print(f"Compiled pipeline: {spec}")
    if not args.submit:
        print("Dry run only. Pass --submit to create a Vertex AI Pipeline job.")
        return

    from google.cloud import aiplatform

    aiplatform.init(project=args.project, location=args.region)
    job = aiplatform.PipelineJob(
        display_name="flightops-insight",
        template_path=str(Path(spec)),
        pipeline_root=args.pipeline_root,
        parameter_values={"config_path": args.config},
        enable_caching=True,
    )
    job.submit()
    print(f"Submitted: {job.resource_name}")


if __name__ == "__main__":
    main()
