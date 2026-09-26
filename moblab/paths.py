"""Filesystem locations. The app is run from a checkout; nothing is installed system-wide."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_DIR = REPO_ROOT / "policies"
ACCEPTANCE_DIR = REPO_ROOT / "acceptance"
EVIDENCE_DIR = REPO_ROOT / "evidence"
DOCS_DIR = REPO_ROOT / "docs"
INSTANCE_DIR = REPO_ROOT / "instance"
DEFAULT_DB_NAME = "merchant_onboarding_lab.db"
DEFAULT_DB_PATH = INSTANCE_DIR / DEFAULT_DB_NAME
