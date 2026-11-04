"""Initialize the fresh MySQL schema before starting the platform server."""

from app.db.mysql import ensure_schema


def main() -> None:
    ensure_schema()


if __name__ == "__main__":
    main()
