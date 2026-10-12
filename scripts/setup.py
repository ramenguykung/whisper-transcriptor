"""Generate local configuration without replacing an existing secret."""
from pathlib import Path
import secrets


def main():
    path = Path(__file__).resolve().parents[1] / ".env"
    if path.exists():
        print("Existing .env kept")
        return
    with path.open("x", encoding="utf-8") as file:
        file.write(f"WORKER_TOKEN={secrets.token_urlsafe(32)}\nWEB_PORT=8080\n")
    print("Created .env. Start with docker compose up --build -d")


if __name__ == "__main__":
    main()
