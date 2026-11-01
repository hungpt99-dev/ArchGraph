.PHONY: help up down logs seed test e2e screenshots clean

help:
	@echo "make up        - build and start the full stack (Docker Compose)"
	@echo "make seed      - load the demo workspace (Hotel Management Platform)"
	@echo "make test      - backend unit + integration tests"
	@echo "make e2e       - Playwright end-to-end tests against the running stack"
	@echo "make screenshots - capture evidence screenshots from the running stack"
	@echo "make down      - stop the stack"

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api worker

seed:
	docker compose --profile demo run --rm seed

test:
	cd backend && ../.venv/bin/python -m pytest tests/ -q

e2e:
	cd frontend && npx playwright test

screenshots:
	cd frontend && npx playwright test tests/screenshots.spec.ts

clean:
	docker compose down -v
