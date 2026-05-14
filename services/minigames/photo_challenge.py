import random
from typing import Dict, Any
from sqlalchemy.orm import Session
from models.apero import Apero
from .base import BaseMiniGame


class PhotoChallengeGame(BaseMiniGame):
    @property
    def game_id(self) -> str:
        return "PHOTO_CHALLENGE"

    def setup_game(self, apero: Apero, db: Session) -> None:
        challenges = [
            # 🤪 Thème Grimaces & Expressions (Les Classiques)
            "Fais ta pire grimace avec un triple menton",
            "Prends un selfie en louchant au maximum",
            "Imite la tête de quelqu'un de constipé",
            "Fais un grand sourire flippant (façon psychopathe)",
            "Tire la langue au maximum pour essayer de toucher ton nez",
            "Gonfle tes joues au maximum comme un poisson-globe",
            "Fais la moue la plus exagérée possible (duck face de l'enfer)",
            "Écarquille les yeux comme si tu venais de voir un fantôme",
            "Affiche ton plus beau regard de séducteur/séductrice raté(e)",
            "Fais la tête de quelqu'un qui vient de se cogner le petit orteil",
            "Prends l'expression de quelqu'un qui vient de lâcher un pet silencieux",

            # 🎭 Thème Acting & Situations
            "Prends l'air le plus snob et méprisant possible",
            "Imite un bébé qui fait un caprice parce qu'il n'a plus de bière",
            "Fais semblant de dormir profondément la bouche grande ouverte",
            "Imite un chat qui crache (montre les dents !)",
            "Fais la tête de quelqu'un qui vient de voir son ex rentrer dans le bar",
            "Joue le rôle d'un influenceur beauté avec une pinte de bière",
            "Imite une statue grecque très dramatique",
            "Prends la pose d'un super-héros qui atterrit au sol",
            "Fais semblant de pleurer toutes les larmes de ton corps",

            # 🍻 Thème Objets & Bar
            "Mets un objet ridicule sur ta tête",
            "Prends un selfie avec le verre le plus vide de la table",
            "Fais un selfie avec une chaussure (pas la tienne) collée à ton visage",
            "Cache-toi derrière un objet, on ne doit voir que tes yeux",
            "Mets un objet inattendu dans ta bouche (comme un stylo ou un sous-bock)",
            "Réussis à avoir le barman ou la serveuse en arrière-plan",
            "Prends une photo de toi à travers le fond de ton verre",
            "Fais un selfie en équilibrant un objet sur ton nez",
            "Prends la photo en tenant ton téléphone à l'envers",

            # 👯 Thème Interactions (Impliquer les autres)
            "Oblige ton voisin de droite à faire exactement la même grimace que toi",
            "Prends un selfie où tu fais semblant de mordre l'épaule de ton voisin",
            "Fais un selfie de groupe où tout le monde fait un doigt d'honneur",
            "Prends une photo où quelqu'un d'autre te tire les joues",
            "Fais un bisou volé (ou baveux) sur la joue de la personne en face",
            "Prends un selfie où tu pointes quelqu'un du doigt avec un air accusateur",
            "Réussis à faire un photobomb sur ton propre selfie grâce à un pote",
            "Échange tes lunettes (ou un vêtement) avec quelqu'un juste pour la photo",
            "Prends la photo pendant que quelqu'un d'autre te verse (virtuellement) à boire"
        ]

        state = dict(apero.current_game_state)
        state["challenge"] = random.choice(challenges)
        state["photo_base64"] = None  # Contiendra la photo prise
        apero.current_game_state = state

    def get_sdui_payload(self, apero: Apero, db: Session) -> Dict[str, Any]:
        state = apero.current_game_state

        player_ids = state.get("player_ids", [])
        turn_index = state.get("turn_index", 0)
        current_player_id = player_ids[turn_index % len(player_ids)]
        current_username = next((p.user.username for p in apero.participants if p.user_id == current_player_id),
                                "Le Photographe")

        # --- CAS 1 : LA PHOTO A ÉTÉ PRISE (VOTE DU GROUPE) ---
        if state.get("photo_base64"):
            return {
                "turn_of": "Le Jury (Tout le monde)",
                "instruction_header": f"Défi de {current_username}",
                "title": "A-t-il respecté le thème ?",
                "description": f"Thème : {state['challenge']}",
                "required_sensor": {
                    "type": "IMAGE_DISPLAY",  # Un simple composant pour afficher une image
                    "image_data": state["photo_base64"]
                },
                "actions": [
                    {"label": "Validé ! (Il distribue 2 gorgées)", "action_id": "VOTE_YES", "style": "success"},
                    {"label": "Nul ! (Il boit 2 gorgées)", "action_id": "VOTE_NO", "style": "danger"}
                ]
            }

        # --- CAS 2 : PHASE DE CAPTURE ---
        return {
            "turn_of": current_username,
            "instruction_header": "Objectif compromettant",
            "title": "Paparazzi 📸",
            "description": f"Tu as 10 secondes pour prendre cette photo : {state['challenge']}",
            "required_sensor": {
                "type": "CAMERA_CAPTURE",
                "facing_mode": "user",  # Demande la caméra frontale (selfie)
                "auto_capture_ms": 10000  # Le front prend la photo tout seul après 10s si le joueur n'a pas cliqué
            },
            "actions": []
        }

    def handle_action(self, apero: Apero, db: Session, action_payload: Dict[str, Any]) -> None:
        action_id = action_payload.get("action_id", "")
        state = dict(apero.current_game_state)

        if action_id == "PHOTO_TAKEN":
            # Le frontend envoie la photo encodée en base64
            state["photo_base64"] = action_payload.get("image_base64")
            apero.current_game_state = state

        elif action_id in ["VOTE_YES", "VOTE_NO"]:
            # Fin du jeu, on incrémente le tour et on passe à la transition
            state["turn_index"] = state.get("turn_index", 0) + 1
            apero.current_game_state = state
            apero.current_game_id = "TURN_TRANSITION"
