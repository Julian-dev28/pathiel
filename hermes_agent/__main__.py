"""hermes-agent entrypoint — starts the FastAPI server via uvicorn."""

import sys
import uvicorn


def main():
    """CLI entrypoint: python -m hermes_agent"""
    import os

    # Load .env if present
    from dotenv import load_dotenv
    load_dotenv()

    host = os.environ.get("HERMES_HOST", "0.0.0.0")
    port = int(os.environ.get("HERMES_PORT", "8000"))

    print(f"Starting Hermes Agent server on {host}:{port}")
    uvicorn.run(
        "hermes_agent.server:app",
        host=host,
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()
