.DEFAULT_GOAL := help
.PHONY: help logs test docker-test stop build up up-view install setup run admin view safety-scan safety-check

help:
	@perl -nle'print $& if m{^[a-zA-Z_-]+:.*?## .*$$}' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}'

install: ## install all Python dependencies (local dev)
	pip install uv 2>/dev/null || true
	uv pip install -r requirements/local.txt

setup: install ## install deps + Playwright browsers + migrate + bootstrap CRM
	playwright install --with-deps chromium
	python manage.py migrate --no-input
	python manage.py setup_crm

run: ## run the daemon
	python manage.py rundaemon

dumpcookies: ## dump your real browser session (saves to DB — daemon never logs in again)
	.venv/bin/python manage.py dumpcookies --profile 1

test: ## run the test suite
	.venv/bin/pytest

safety-scan: ## static account-safety scan only (no tests)
	.venv/bin/python scripts/account_safety.py --all

safety-check: safety-scan ## account-safety gate: static scan + full test suite (network blocked in tests)
	.venv/bin/pytest

admin: ## start the Django Admin web server
	@echo ""
	@echo "  Django Admin: http://localhost:8000/admin/"
	@echo "  No superuser yet? Run: python manage.py createsuperuser"
	@echo ""
	python manage.py runserver

# Docker targets
logs: ## follow the logs of the service
	docker compose -f local.yml logs -f

docker-test: ## run tests in Docker
	docker compose -f local.yml run --remove-orphans app py.test -vv -p no:cacheprovider

stop: ## stop all services defined in Docker Compose
	docker compose -f local.yml stop

build: ## build all services defined in Docker Compose
	docker compose -f local.yml build

up: ## run the defined service in Docker Compose
	docker compose -f local.yml up --build -d
	docker compose -f local.yml logs -f

up-view: ## run the defined service in Docker Compose and open vinagre
	docker compose -f local.yml up --build -d
	sleep 3
	$(MAKE) view
	docker compose -f local.yml logs -f app

view: ## open vinagre to view the app
	@sh -c 'vinagre vnc://127.0.0.1:5900 > /dev/null 2>&1 &'
