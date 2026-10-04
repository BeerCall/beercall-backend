import pytest
from scripts.schema_fingerprint import clean_name, canonize_default, dict_diff

def test_identique():
    d1 = {"tables": {"users": {"columns": {"id": {"type": "integer"}}}}}
    d2 = {"tables": {"users": {"columns": {"id": {"type": "integer"}}}}}
    assert dict_diff(d1, d2) == {}

def test_index_manquant():
    d1 = {"tables": {"users": {"indexes": [{"name": "idx_1"}]}}}
    d2 = {"tables": {"users": {"indexes": []}}}
    diff = dict_diff(d1, d2)
    assert diff != {}

def test_version_differente():
    d1 = {"alembic_version": ["123"]}
    d2 = {"alembic_version": ["456"]}
    diff = dict_diff(d1, d2)
    assert diff["alembic_version"] == {"old": ["123"], "new": ["456"]}

def test_now_equivalent():
    assert canonize_default("CURRENT_TIMESTAMP") == "now()"
    assert canonize_default("now()") == "now()"
    assert canonize_default("(now())") == "now()"
    
def test_casts_texte_equivalents():
    assert canonize_default("'x'::text") == "'x'::text"
    assert canonize_default("'x'::character varying") == "'x'::text"
    assert canonize_default("'test'::text") == "'test'::text"

def test_longueur_varchar_differente():
    d1 = {"tables": {"users": {"columns": {"name": {"type": "varchar(50)"}}}}}
    d2 = {"tables": {"users": {"columns": {"name": {"type": "varchar(255)"}}}}}
    diff = dict_diff(d1, d2)
    assert diff["tables"]["users"]["columns"]["name"]["type"]["old"] == "varchar(50)"
    
def test_labels_enum_differents():
    d1 = {"tables": {"t": {"columns": {"e": {"type": "enum (A,B)"}}}}}
    d2 = {"tables": {"t": {"columns": {"e": {"type": "enum (B,A)"}}}}}
    diff = dict_diff(d1, d2)
    assert diff != {}

def test_ordre_index_different():
    d1 = {"tables": {"users": {"indexes": [{"name": "idx", "columns": ["a", "b"], "unique": False}]}}}
    d2 = {"tables": {"users": {"indexes": [{"name": "idx", "columns": ["b", "a"], "unique": False}]}}}
    diff = dict_diff(d1, d2)
    assert diff != {}
