"""Conector de GitHub falso del ciclo: la protección de la rama `infra` se quita por entorno.

Se ejecuta en un subproceso `spawn`, como en producción: por eso vive en un módulo importable.
"""

import os
from datetime import datetime

from checks.acceptance_helpers import LIVE
from checks.test_expected import gh_evidence
from connectors.github.check_data import branch_item, effective
from reports.acceptance_fakes import COLLECTED_AT_ENV, GitHubFake

UNPROTECTED_ENV = "GUIDE_TABLE_UNPROTECTED"


class GitHubBranchSwitch(GitHubFake):
    def collect(self, config):
        if not os.environ.get(UNPROTECTED_ENV):
            return super().collect(config)
        evidence = gh_evidence(
            LIVE, collected_at=datetime.fromisoformat(os.environ[COLLECTED_AT_ENV])
        )
        items = evidence.data["sections"]["branch_rules"]["items"]
        items[:] = [
            branch_item("infra", rulesets=[], rules=effective())
            if i["repository"] == "infra"
            else i
            for i in items
        ]
        return evidence
