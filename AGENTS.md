# OpenCode System Prompt - BeerCall Backend

Ce fichier définit les directives strictes et l'architecture pour le développement du backend BeerCall.

## Tests — E2E uniquement (décision utilisateur du 16/11/2026)

- **Ne créer, réintroduire ni lancer de TU**, notamment Pytest unitaires ou tests isolés avec doubles de services/DB. Les TU existants sont à supprimer dans la migration prioritaire, pas à rejouer avant celle-ci.
- Vérifier les risques significatifs par les parcours E2E réels (application/API/DB/services nécessaires), notamment permissions, intégrité des données et effets métier. Compléter les E2E plutôt qu'ajouter des TU.
- E2E seuls : **≥75 % des lignes backend ET ≥75 % frontend**, séparément, sur le périmètre instrumenté existant ; aucun abaissement de seuil, nouvelle exclusion de code vivant ou cumul de campagnes.
- Suite E2E complète **≤15 minutes**, préparation/build/seed mesurés et bornés séparément ; ressources plafonnées, arrêt vérifié sur annulation. Aucune relance lourde ou reconstruction de dev sans GO.
- Conserver les contrôles statiques et les fixtures/supports utiles aux E2E. Cette décision prévaut sur les anciens plans/rapports demandant des TU.
- Migration prioritaire avant toute nouvelle qualification ; plan externe `C:/Users/Migz3rrr/.opencode/plan/beercall/plan-grand-menage-apres-deploiement.md`, §12k.

## 🏗 Architecture & Design Patterns
- **Type** : API REST développée en Python.
- **Modèle en Couches (Séparation stricte des responsabilités)** :
  - `api/v1/` : Routeurs et Controllers. **Ne doit contenir aucune logique métier**. Ils réceptionnent la requête et appellent les services.
  - `services/` : Contient toute la logique métier, traitement d'images (YOLO) et envois externes (Firebase Notifications).
  - `models/` & `db/` : Modèles SQLAlchemy (ORM) et gestion des sessions DB.
  - `schemas/` : Validation des données entrantes/sortantes via Pydantic.
  - `alembic/` : Gestion des migrations de base de données.

## 🛠 Stack Technique
- **Framework** : FastAPI, serveur Uvicorn.
- **Base de données** : PostgreSQL via SQLAlchemy et Alembic.
- **IA & Traitement** : Ultralytics (YOLOv8) pour validation d'images, Google GenAI.
- **Tâches en arrière-plan** : APScheduler.
- **Notifications** : Firebase Admin SDK.

## 📏 Conventions de Codage
- **Typage Strict** : Type hinting Python obligatoire pour toutes les fonctions (`typing`, Pydantic).
- **Nommage** : Fichiers, variables, et fonctions en `snake_case`. Les classes en `PascalCase`.
- **Migrations BDD** : Tout ajout ou modification dans `models/` nécessite la génération d'une migration via `alembic revision --autogenerate -m "description"`.

## 🔒 Sécurité & Gestion des Erreurs
- **Authentification** : Gestion des tokens JWT custom (`core/security.py`) utilisant `OAuth2PasswordBearer`. Hashage avec `bcrypt` (Passlib).
- **Gestion des Secrets** : Ne jamais hardcoder de clés. Toujours utiliser `.env` ou `os.getenv()`.
- **Gestion des Erreurs** : Les services lèvent uniquement des exceptions du domaine (`LookupError`, `ValueError`, `PermissionError`), sans dépendance FastAPI. Les routeurs les traduisent en `HTTPException(status_code=X, detail="Message")` et orchestrent les effets de bord après commit. Ne jamais laisser fuiter des stack traces brutes.
