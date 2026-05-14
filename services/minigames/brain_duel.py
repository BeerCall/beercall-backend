import random
from typing import Dict, Any
from sqlalchemy.orm import Session
from models.apero import Apero
from .base import BaseMiniGame


class BrainDuelGame(BaseMiniGame):
    @property
    def game_id(self) -> str:
        return "BRAIN_DUEL"

    def setup_game(self, apero: Apero, db: Session) -> None:
        state = dict(apero.current_game_state)
        player_ids = state.get("player_ids", [])

        # On s'assure d'avoir au moins 2 joueurs
        if len(player_ids) >= 2:
            # On prend le joueur dont c'est le tour
            p1_id = player_ids[state.get("turn_index", 0) % len(player_ids)]
            # Et on tire un adversaire au hasard parmi les autres
            opponents = [pid for pid in player_ids if pid != p1_id]
            p2_id = random.choice(opponents)
        else:
            p1_id, p2_id = player_ids[0], player_ids[0]

        state["duel_p1_id"] = p1_id
        state["duel_p2_id"] = p2_id
        state["winner"] = None

        apero.current_game_state = state

    def get_sdui_payload(self, apero: Apero, db: Session) -> Dict[str, Any]:
        state = apero.current_game_state

        # Récupération des pseudos
        p1_name = next((p.user.username for p in apero.participants if p.user_id == state.get("duel_p1_id")),
                       "Joueur 1")
        p2_name = next((p.user.username for p in apero.participants if p.user_id == state.get("duel_p2_id")),
                       "Joueur 2")

        # Si le duel est fini, on affiche le résultat
        if state.get("winner"):
            winner_name = p1_name if state["winner"] == "P1" else p2_name
            loser_name = p2_name if state["winner"] == "P1" else p1_name
            return {
                "turn_of": "Fin du Duel ⚔️",
                "instruction_header": "Résultat",
                "title": f"{winner_name} l'emporte !",
                "description": f"{loser_name}, tu es trop lent... Bois 2 gorgées !",
                "required_sensor": {"type": "BUTTONS"},
                "actions": [
                    {"label": "On passe à la suite", "action_id": "NEXT_TURN", "style": "primary"}
                ]
            }

        # Sinon, on lance le composant de Duel
        return {
            "turn_of": f"{p1_name} VS {p2_name}",
            "instruction_header": "Posez le téléphone à plat entre vous",
            "title": "Duel de Réflexes ⚡",
            "description": "Le premier qui tape quand l'écran devient VERT a gagné !",
            "required_sensor": {
                "type": "DUEL_SPLIT_SCREEN",
                "player_top": p2_name,
                "player_bottom": p1_name,
                "signal_delay_ms": random.randint(2000, 6000)  # Le front attendra ce délai avant de dire GO
            },
            "actions": []
        }

    def handle_action(self, apero: Apero, db: Session, action_payload: Dict[str, Any]) -> None:
        action_id = action_payload.get("action_id", "")
        state = dict(apero.current_game_state)

        if action_id == "WINNER_TOP":
            state["winner"] = "P2"
            apero.current_game_state = state
        elif action_id == "WINNER_BOTTOM":
            state["winner"] = "P1"
            apero.current_game_state = state
        elif action_id == "NEXT_TURN":
            state["turn_index"] = state.get("turn_index", 0) + 1
            apero.current_game_state = state
            apero.current_game_id = "TURN_TRANSITION"
