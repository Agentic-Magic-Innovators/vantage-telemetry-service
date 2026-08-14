"""Deterministic test secrets, set before any test module imports app.* --
app/secrets.py now refuses to start without a real TELEMETRY_INTERNAL_KEY /
TELEMETRY_BRIDGE_JWT_SECRET, so the test suite needs its own values rather
than the VANTAGE_ALLOW_DEV_SECRETS escape hatch meant for manual local runs.
"""
import os

os.environ.setdefault("TELEMETRY_INTERNAL_KEY", "test-internal-key-not-for-deploy")
os.environ.setdefault("TELEMETRY_BRIDGE_JWT_SECRET", "test-bridge-secret-not-for-deploy")
