.PHONY: help dev dev-up dev-down test lint migrate build clean

help:
	@echo "RAC Control Plane development commands:"
	@echo ""
	@echo "  make dev          - Start development services (postgres + mock-oidc + control-plane)"
	@echo "  make dev-up       - Start only supporting services (postgres + mock-oidc)"
	@echo "  make dev-down     - Stop all development services"
	@echo "  make test         - Run Python backend tests"
	@echo "  make lint         - Run linter and type checker on backend"
	@echo "  make migrate      - Run Alembic migrations"
	@echo "  make build        - Build Docker image"
	@echo "  make clean        - Remove built artifacts and stop containers"

# Start full development environment with docker-compose
dev: dev-up build
	docker compose -f docker-compose.dev.yml up control-plane

# Start supporting services only (postgres + mock-oidc)
dev-up:
	docker compose -f docker-compose.dev.yml up -d postgres mock-oidc

# Stop all development services
dev-down:
	docker compose -f docker-compose.dev.yml down

# Run Python backend tests
test:
	cd backend && uv run pytest -v

# Run linter and type checker
lint:
	cd backend && uv run ruff check src tests
	cd backend && uv run mypy src

# Run Alembic migrations
migrate:
	cd backend && uv run alembic upgrade head

# Build Docker image
build:
	docker build -t rac-control-plane:dev .

# Clean up
clean:
	docker compose -f docker-compose.dev.yml down
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf backend/dist build *.egg-info
	docker image rm rac-control-plane:dev 2>/dev/null || true
