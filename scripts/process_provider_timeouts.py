import json

from app.database import SessionLocal
from app.services.provider_timeout_service import (
    process_provider_timeouts,
)


def main() -> None:
    with SessionLocal() as db:
        result = process_provider_timeouts(
            db
        )

    print(
        json.dumps(
            {
                "checked":
                    result.checked,
                "timed_out_request_ids":
                    result
                    .timed_out_request_ids,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
