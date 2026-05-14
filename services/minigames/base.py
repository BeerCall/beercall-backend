from abc import ABC, abstractmethod
from typing import Dict, Any
from sqlalchemy.orm import Session
from models.apero import Apero


class BaseMiniGame(ABC):
    @property
    @abstractmethod
    def game_id(self) -> str:
        """Identifiant unique du jeu (ex: 'HOT_POTATO')"""
        pass

    def setup_game(self, apero: Apero, db: Session) -> None:
        """
        Méthode optionnelle pour initialiser les données spécifiques
        au mini-jeu avant qu'il ne commence.
        """
        pass

    @abstractmethod
    def get_sdui_payload(self, apero: Apero, db: Session) -> Dict[str, Any]:
        """Génère le fameux JSON qui dit au Frontend quoi afficher."""
        pass

    @abstractmethod
    def handle_action(self, apero: Apero, db: Session, action_payload: Dict[str, Any]) -> None:
        """Traite l'action envoyée par le Frontend et met à jour current_game_state."""
        pass
