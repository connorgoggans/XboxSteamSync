import os
import tempfile
from pathlib import Path

# Keep tests away from the real data folder; must be set before xboxsteamsync.paths is imported.
os.environ["XSS_HOME"] = tempfile.mkdtemp(prefix="xss-test-")

FIXTURES = Path(__file__).parent / "fixtures"
