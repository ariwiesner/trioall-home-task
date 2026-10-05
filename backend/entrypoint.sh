#!/bin/sh
set -e

until python manage.py migrate --noinput; do
  echo "Database not ready yet, retrying in 1s..."
  sleep 1
done

python manage.py createsuperuser --noinput || true
python manage.py seed_demo_data

exec python manage.py runserver 0.0.0.0:8000
