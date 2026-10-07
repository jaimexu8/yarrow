#!/bin/sh
# Apply pending database migrations before starting the command.
# `alembic upgrade head` is a no-op when the schema is already at head.
# The retry covers a database that is not accepting connections yet and a
# concurrent container applying the same migrations at the same time.
set -e

for attempt in $(seq 1 30); do
    if alembic upgrade head; then
        break
    fi
    if [ "$attempt" -eq 30 ]; then
        echo "entrypoint: giving up after 30 failed migration attempts" >&2
        exit 1
    fi
    echo "entrypoint: migration attempt $attempt failed, retrying in 2s" >&2
    sleep 2
done

exec "$@"
