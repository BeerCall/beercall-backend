# beercall-backend

> **Dette Supply-Chain** : Les tags d'images de base (ex: `python:3.13-slim`) restent actuellement mutables. Il conviendra de les remplacer par des digests (`@sha256:...`) ultérieurement.

## Installation et Tests

1. Créer et activer un environnement virtuel (venv) :
   ```bash
   python -m venv venv
   source venv/bin/activate  # Sous Linux/Mac
   venv\Scripts\activate     # Sous Windows
   ```

2. Installer les dépendances applicatives et de test :
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   ```

3. Variables d'environnement factices pour les tests :
   Pour exécuter les tests, vous devez définir une clé secrète factice.
   ```bash
   export BEERCALL_SECRET_KEY="test_secret"
   ```
   *Sous Windows (PowerShell) :* `$env:BEERCALL_SECRET_KEY="test_secret"`

4. Exécuter les tests :
   ```bash
   python -m pytest
   ```
5. Exécuter les tests de migration (Nécessite PostgreSQL) :
   Les tests Alembic nécessitent une base PostgreSQL éphémère.
   L'URL doit obligatoirement contenir eercall_test pour éviter toute destruction accidentelle.
   `ash
   export TEST_DATABASE_URL=" postgresql+psycopg://beercall:beercall@localhost:55432/beercall_test\n