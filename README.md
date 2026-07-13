# Evora-engine

## Prerequisites
- Docker & Docker Compose

## Quick Start
```bash
docker compose -f deploy/docker/docker-compose.yaml up -d
```
App runs at `http://localhost:8000`

## Useful Commands
| Command | Description |
|---|---|
| `docker compose -f deploy/docker/docker-compose.yaml up -d` | Start all services |
| `docker compose -f deploy/docker/docker-compose.yaml down` | Stop all services |
| `docker compose -f deploy/docker/docker-compose.yaml run --rm app sh -c "python manage.py <command>"` | Run Django commands |
| `docker compose -f deploy/docker/docker-compose.yaml build` | Rebuild the app image |

## Services
- **app** — Django 6.0 + DRF on port 8000
- **db** — PostgreSQL 16

## Environment Variables
| Variable | Description | Default |
|---|---|---|
| `DB_HOST` | Database host | `db` |
| `DB_NAME` | Database name | `devdb` |
| `DB_USER` | Database user | `devuser` |
| `DB_PASS` | Database password | `changeme` |

## Development
- Code lives in `app/` and is mounted as a volume — changes take effect immediately (hot-reload).
- To run Django commands: `docker compose -f deploy/docker/docker-compose.yaml run --rm app sh -c "python manage.py <command>"`
- To create a new app: `docker compose -f deploy/docker/docker-compose.yaml run --rm app sh -c "python manage.py startapp <app_name>"`
