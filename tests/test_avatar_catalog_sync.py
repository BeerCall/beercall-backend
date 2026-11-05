from datetime import datetime, timezone

from models.gamification import Skin, user_skins
from models.user import User
from services.avatar_catalog import CatalogItem, CatalogResult
from services.avatar_catalog_sync import sync_catalog

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)


def _user(db) -> User:
    user = User(username="catalog-sync-user", hashed_password="pw", capsules=0, avatar_config={})
    db.add(user)
    db.flush()
    return user


def _skin(db, skin_id: str, name: str = "X", price: int = 0, source: str | None = None) -> Skin:
    skin = Skin(
        id=skin_id,
        name=name,
        gender="Men",
        category="head",
        price_caps=price,
        source=source,
        available=True,
    )
    db.add(skin)
    db.flush()
    return skin


def _item(source_id: str, source: str, price: int) -> CatalogItem:
    return CatalogItem(
        id=source_id,
        source=source,
        gender="Men",
        category="head",
        name="X",
        key="Men_head_X",
        price_caps=price,
        display_name="Tête X",
        asset_url=f"/models/glb/v1/pieces/Men/{source_id}.1234abcd.glb",
        sha256="a" * 64,
        bytes=1,
    )


def test_rename_updates_same_row_and_keeps_user_links(db_session):
    user = _user(db_session)
    row = _skin(
        db_session,
        "Men_head_X_500",
        price=500,
        source="Men_head_X_500.fbx",
    )
    user.skins.append(row)
    db_session.flush()
    before_links = [
        link.skin_id
        for link in db_session.query(user_skins).filter_by(user_id=user.id).all()
    ]

    report = sync_catalog(
        db_session,
        CatalogResult(items=[_item("Men_head_X_800", "Men_head_X_800.fbx", 800)]),
        now=NOW,
    )

    db_session.refresh(row)
    assert report.updated == ["Men_head_X_500"]
    assert report.created == []
    assert row.id == "Men_head_X_500"
    assert row.price_caps == 800
    assert row.source == "Men_head_X_800.fbx"
    assert row.available is True
    assert [
        link.skin_id
        for link in db_session.query(user_skins).filter_by(user_id=user.id).all()
    ] == before_links


def test_orphan_with_same_key_is_hidden_not_adopted(db_session):
    user = _user(db_session)
    orphan = _skin(db_session, "Men_head_X_600", price=0, source=None)
    kept = _skin(
        db_session,
        "Men_head_X_5000",
        price=5000,
        source="Men_head_X_5000.fbx",
    )
    user.skins.extend([orphan, kept])
    db_session.flush()
    before_links = {
        link.skin_id
        for link in db_session.query(user_skins).filter_by(user_id=user.id).all()
    }

    report = sync_catalog(
        db_session,
        CatalogResult(items=[_item("Men_head_X_5000", "Men_head_X_5000.fbx", 5000)]),
        now=NOW,
    )

    db_session.refresh(orphan)
    db_session.refresh(kept)
    assert report.updated == ["Men_head_X_5000"]
    assert report.hidden == ["Men_head_X_600"]
    assert report.conflicts == []
    assert orphan.available is False
    assert orphan.id == "Men_head_X_600"
    assert orphan.price_caps == 0
    assert orphan.source is None
    assert kept.available is True
    assert {
        link.skin_id
        for link in db_session.query(user_skins).filter_by(user_id=user.id).all()
    } == before_links


def test_two_rows_same_key_without_exact_id_is_a_conflict_and_changes_nothing(db_session):
    first = _skin(db_session, "Men_head_X_500", price=500, source="Men_head_X_500.fbx")
    second = _skin(db_session, "Men_head_X_600", price=600, source="Men_head_X_600.fbx")
    before = [
        (skin.id, skin.price_caps, skin.source, skin.available)
        for skin in (first, second)
    ]

    report = sync_catalog(
        db_session,
        CatalogResult(items=[_item("Men_head_X_800", "Men_head_X_800.fbx", 800)]),
        now=NOW,
    )

    db_session.refresh(first)
    db_session.refresh(second)
    assert report.created == []
    assert report.updated == []
    assert report.hidden == []
    assert len(report.conflicts) == 1
    assert report.conflicts[0].key == "Men_head_X"
    assert set(report.conflicts[0].skin_ids) == {"Men_head_X_500", "Men_head_X_600"}
    assert [
        (skin.id, skin.price_caps, skin.source, skin.available)
        for skin in (first, second)
    ] == before


def test_dry_run_reports_plan_without_writing(db_session):
    row = _skin(db_session, "Men_head_X_500", price=500, source="Men_head_X_500.fbx")

    report = sync_catalog(
        db_session,
        CatalogResult(items=[_item("Men_head_X_800", "Men_head_X_800.fbx", 800)]),
        dry_run=True,
        now=NOW,
    )

    db_session.refresh(row)
    assert report.updated == ["Men_head_X_500"]
    assert row.price_caps == 500
    assert row.source == "Men_head_X_500.fbx"


def test_missing_file_hides_existing_row_without_deleting_it(db_session):
    user = _user(db_session)
    row = _skin(db_session, "Men_head_X_500", price=500, source="Men_head_X_500.fbx")
    user.skins.append(row)
    db_session.flush()

    report = sync_catalog(db_session, CatalogResult(items=[]), now=NOW)

    db_session.refresh(row)
    assert report.hidden == ["Men_head_X_500"]
    assert row.available is False
    assert db_session.query(Skin).filter_by(id="Men_head_X_500").one() is row
    assert db_session.query(user_skins).filter_by(user_id=user.id).count() == 1


def test_new_file_creates_row_with_current_source(db_session):
    report = sync_catalog(
        db_session,
        CatalogResult(items=[_item("Men_head_X_500", "Men_head_X_500.fbx", 500)]),
        now=NOW,
    )

    created = db_session.query(Skin).filter_by(id="Men_head_X_500").one()
    assert report.created == ["Men_head_X_500"]
    assert created.price_caps == 500
    assert created.source == "Men_head_X_500.fbx"
    assert created.available is True
