#!/bin/bash

echo "🚀 Exécution des migrations Alembic sur la DB de Prod..."
alembic upgrade head

echo "🍻 Démarrage du serveur BeerCall..."
exec uvicorn main:app --host 0.0.0.0 --port 8000