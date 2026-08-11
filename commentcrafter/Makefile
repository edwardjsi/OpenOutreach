.DEFAULT_GOAL := help
.PHONY: help setup run test

help:
	@perl -nle'print $& if m{^[a-zA-Z_-]+:.*?## .*$$}' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}'

setup: ## Install deps + Playwright browsers + init DB
	pip install -r requirements.txt
	playwright install --with-deps chromium
	python -m src.main setup

run: ## Run the commenter daemon
	python -m src.main run

login: ## Re-login and refresh cookies
	python -m src.main login

status: ## Show stats (comments today, queue size, rate limits)
	python -m src.main status

add-target: ## Add a LinkedIn profile to monitor
	python -m src.main add-target $(filter-out $@,$(MAKECMDGOALS))

add-topic: ## Add a topic/keyword to monitor
	python -m src.main add-topic $(filter-out $@,$(MAKECMDGOALS))
