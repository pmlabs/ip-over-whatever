all: help

help:
	@echo "up"
	@echo "down"
	@echo "box1-connect (box1-conn, box1-screen)"
	@echo "box2-connect (box2-conn, box2-screen)"

up:
	docker compose up -d

down:
	docker compose down

box1-conn: box1-connect
box1-screen: box1-connect
box1-connect:
	docker compose exec -it box1 screen -RD bash


box2-conn: box2-connect
box2-screen: box2-connect
box2-connect:
	docker compose exec -it box2 screen -RD bash