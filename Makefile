.PHONY: test lint typecheck run docker-up docker-down

test:
	python -m pytest

lint:
	ruff check .

typecheck:
	mypy src

run:
	uvicorn agent.main:app --reload --port 8000

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down