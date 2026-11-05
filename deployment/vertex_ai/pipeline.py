"""Vertex AI Pipelines skeleton for FlightOps Insight.

This module is a template. It imports KFP only when ``build_pipeline`` is called, so
the repository does not require KFP for local development. The component bodies call
the same package entrypoints used by ``scripts/``; in a real deployment each component
would run in the pinned container image.
"""

from __future__ import annotations

from typing import Any


def build_pipeline(image: str):
    """Build a KFP v2 pipeline. Requires ``kfp`` in the environment."""
    try:
        from kfp import dsl
    except ImportError as exc:  # pragma: no cover - template path
        raise RuntimeError("Install kfp to compile the Vertex AI pipeline") from exc

    @dsl.component(base_image=image)
    def validate_and_build(config_path: str) -> str:
        import subprocess

        subprocess.run(["python", "scripts/build_data.py"], check=True)
        return config_path

    @dsl.component(base_image=image)
    def train_otp(config_path: str) -> str:
        import subprocess

        subprocess.run(["python", "scripts/train_otp.py", "--force"], check=True)
        return "otp"

    @dsl.component(base_image=image)
    def train_rul(config_path: str) -> str:
        import subprocess

        subprocess.run(["python", "scripts/train_rul.py", "--force"], check=True)
        return "rul"

    @dsl.component(base_image=image)
    def optimize_maintenance(config_path: str) -> str:
        import subprocess

        subprocess.run(["python", "scripts/run_optimization.py", "--force"], check=True)
        return "optimization"

    @dsl.pipeline(name="flightops-insight", description="OTP + RUL + maintenance optimization")
    def pipeline(config_path: str = "/app/config.yaml") -> None:
        data = validate_and_build(config_path=config_path)
        otp = train_otp(config_path=data.output)
        rul = train_rul(config_path=data.output)
        optimize_maintenance(config_path=data.output).after(otp, rul)

    return pipeline


def compile_pipeline(output_path: str, image: str) -> str:
    """Compile the pipeline to YAML and return the path."""
    from kfp import compiler

    compiler.Compiler().compile(build_pipeline(image), output_path)
    return output_path
