.PHONY: bot dashboard fmt install

bot:
	python -m bot.main

dashboard:
	gunicorn --chdir dashboard/website --bind 0.0.0.0:8501 --workers 2 --threads 4 app:app

install:
	pip install -r bot/requirements.txt -r dashboard/website/requirements.txt

fmt:
	black bot/
