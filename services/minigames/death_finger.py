import random
from typing import Dict, Any
from sqlalchemy.orm import Session
from models.apero import Apero
from .base import BaseMiniGame


class DeathFingerGame(BaseMiniGame):
    @property
    def game_id(self) -> str:
        return "DEATH_FINGER"

    def setup_game(self, apero: Apero, db: Session) -> None:
        # On choisit la punition aléatoirement
        punishments = [
            "boire 3 gorgées",
            "finir son verre",
            "distribuer 4 gorgées",
            "faire un cul-sec",
            "garder le doigt sur le nez jusqu'au prochain tour"
        ]

        state = dict(apero.current_game_state)
        state["punishment"] = random.choice(punishments)
        state["selection_done"] = False
        apero.current_game_state = state

    def get_sdui_payload(self, apero: Apero, db: Session) -> Dict[str, Any]:
        state = apero.current_game_state

        # --- CAS 1 : LE DOIGT A ÉTÉ CHOISI ---
        if state.get("selection_done"):
            return {
                "game_id": self.game_id,
                "turn_of": "Le sort en est jeté ☠️",
                "instruction_header": "Résultat",
                "title": "C'est toi !",
                "description": f"La personne dont le doigt a été choisi doit {state.get('punishment')} !",
                "required_sensor": {"type": "BUTTONS"},
                "actions": [
                    {"label": "C'est fait, au suivant", "action_id": "NEXT_TURN", "style": "primary"}
                ]
            }

        # --- CAS 2 : ON POSE LES DOIGTS ---
        return {
            "game_id": self.game_id,
            "turn_of": "Tout le monde",
            "instruction_header": "Posez l'appareil au centre",
            "title": "Le Doigt de la Mort 🖐️",
            "description": "Que tous ceux qui veulent participer posent un doigt sur l'écran et le maintiennent !",
            "required_sensor": {
                "type": "MULTI_TOUCH_TRACKER",
                "hold_duration_ms": 3000,  # Le front attend 3s sans mouvement avant de lancer la roulette
            },
            "actions": []
        }

    def handle_action(self, apero: Apero, db: Session, action_payload: Dict[str, Any]) -> None:
        action_id = action_payload.get("action_id", "")
        state = dict(apero.current_game_state)

        if action_id == "TARGET_SELECTED":
            # Le frontend a fini son animation et a choisi un doigt
            state["selection_done"] = True
            apero.current_game_state = state

        elif action_id == "NEXT_TURN":
            # Fin du jeu, on incrémente le tour et on repasse à la transition
            state["turn_index"] = state.get("turn_index", 0) + 1
            apero.current_game_state = state
            apero.current_game_id = "TURN_TRANSITION"
