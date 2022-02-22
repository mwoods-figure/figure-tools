#!/usr/bin/env bash

set -e

NAME=pgpg
PORT=$(shuf -i 2000-65000 -n 1)
PASSWORD=password

function cleanup() {
  INSTANCE=$(docker ps --filter="name=pgpg" -q)
  docker stop $INSTANCE
  docker rm $INSTANCE
}

trap cleanup EXIT

echo "Running $NAME:$PORT"

docker run -d \
  --name=$NAME \
  -p $PORT:$PORT \
  -e POSTGRES_PASSWORD=$PASSWORD \
  -e PGPORT=$PORT \
  postgres

sleep 2

PGPASSWORD=$PASSWORD psql -h localhost -U postgres -p $PORT postgres

