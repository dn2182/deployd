"""The vendored CI script must sign exactly like the server verifies."""
import importlib.util
from pathlib import Path

from deployd.security import compute_signature

spec = importlib.util.spec_from_file_location(
    "notify_deploy", Path(__file__).parent.parent / "examples" / "notify_deploy.py"
)
notify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notify)


def test_signature_parity():
    secret, ts, body = "abc", "1756100000", b'{"app":"x"}'
    assert notify.sign(secret, ts, body) == compute_signature(secret, ts, body)
