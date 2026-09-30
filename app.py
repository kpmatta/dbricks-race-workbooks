"""Databricks Apps entry point for RACE."""

import logging
import os

import uvicorn

from race.main import create_app

app = create_app()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    uvicorn.run(
        app,
        host="0.0.0.0" if os.getenv("DATABRICKS_APP_NAME") else "127.0.0.1",
        port=int(os.getenv("DATABRICKS_APP_PORT", os.getenv("PORT", "8000"))),
    )
