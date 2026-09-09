import os

# Keep the test suite deterministic, offline, and free of provider API charges.
# This must run during test collection, before test modules import app.main.
os.environ["SAG_PROVIDER"] = "fake"
