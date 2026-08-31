.PHONY: bot dashboard fmt install

bot:
	python -m bot.main

dashboard:
	streamlit run dashboard/app.py

install:
	pip install -r bot/requirements.txt -r dashboard/requirements.txt

fmt:
	black bot/
