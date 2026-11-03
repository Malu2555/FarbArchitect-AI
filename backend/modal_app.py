"""Modal ASGI entrypoint for fabArchitect AI.

Deploy::

    modal secret create llm-keys \\
        META_LLAMA_KEY=... FIREWORKS_API_KEY=... \\
        FIREWORKS_MODEL_ID=accounts/fireworks/models/muse-spark-open \\
        FIREWORKS_MAX_TOKENS=4096 FIREWORKS_REASONING_EFFORT=low \\
        FIREWORKS_IMAGE_MODEL_ID=accounts/fireworks/models/muse-spark-open \\
        CORS_ALLOWED_ORIGINS=https://<your-app>.vercel.app
    modal deploy backend/modal_app.py

Ephemeral dev server::

    modal serve backend/modal_app.py

The Django app is booted entirely from ``backend/app.py`` (no manage.py, no DB).
Secrets come from the Modal Secret named ``llm-keys`` — never from a file.
"""

from __future__ import annotations

import modal

# Deployment revision marker: 2026-11-03-model-swap.
# Bumped to force Modal to create a fresh function version after a
# secret-content change (CORS hotfix, model swap, etc.). Modal's deploy
# fingerprint hashes the app definition (code + secret *names*) but not secret
# *contents*, so a secret value change alone otherwise resolves to
# "Deployment skipped: no changes detected!" and leaves the previous warm
# container holding the stale env.

# ---------------------------------------------------------------------------
# Image: minimal Debian + our exact requirements + the backend sources
# ---------------------------------------------------------------------------
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements("requirements.txt")
    .add_local_python_source("app", "api", "agents", "core")
)

app = modal.App("farbarchitect-ai", image=image)


# ---------------------------------------------------------------------------
# Single ASGI endpoint: Django Ninja under Modal's proxy
# ---------------------------------------------------------------------------
@app.function(
    secrets=[modal.Secret.from_name("llm-keys")],
    cpu=1.0,
    memory=1024,
    timeout=180,
)
@modal.concurrent(max_inputs=8)
@modal.asgi_app()
def web():
    """Import the in-memory Django app lazily so Modal can boot fast."""
    import app as backend  # noqa: F401  (settings.configure + router registration)

    return backend.application
