"""Deployment dry-run validation command."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.deployment.validation import DeployValidationResult, format_deploy_validation, validate_deployment


@dataclass(frozen=True)
class DeployValidateCommandResult:
    result: DeployValidationResult

    @property
    def ok(self) -> bool:
        return self.result.ok


def run_deploy_validate(
    root: Path,
    *,
    stream: TextIO | None = None,
    config_context: RuntimeConfigContext | None = None,
) -> DeployValidateCommandResult:
    output = stream or sys.stdout
    result = validate_deployment(root, config_context=config_context or load_config(root))
    output.write(format_deploy_validation(result))
    output.flush()
    return DeployValidateCommandResult(result=result)


__all__ = ["DeployValidateCommandResult", "run_deploy_validate"]
