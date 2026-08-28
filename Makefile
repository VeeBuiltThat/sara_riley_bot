.PHONY: bot dashboard fmt test

bot:
	go run ./cmd/bot

dashboard:
	streamlit run dashboard/app.py

fmt:
	gofmt -w ./cmd ./internal

test:
	go test ./...
