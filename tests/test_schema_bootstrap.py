import sys
import pytest

def test_no_create_all_on_import(monkeypatch):
    """
    Vérifier que l'import de main.py ne déclenche pas Base.metadata.create_all
    et que l'application peut se charger sans DDL destructif.
    """
    calls = []
    
    # Mocking create_all
    def mock_create_all(*args, **kwargs):
        calls.append("create_all")
    
    from db.database import Base
    monkeypatch.setattr(Base.metadata, "create_all", mock_create_all)
    
    # Supprimer main des modules chargés pour forcer un re-import
    if "main" in sys.modules:
        del sys.modules["main"]
        
    import main
    
    assert "create_all" not in calls, "Base.metadata.create_all a été appelé lors de l'import de main.py"
    assert hasattr(main, "app")
