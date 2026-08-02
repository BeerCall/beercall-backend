import os
import random
import base64
from typing import Dict, Any, Tuple

# Nouveaux imports du SDK
from google import genai
from google.genai import types
from sqlalchemy.orm import Session

from models.apero import Apero
from .base import BaseMiniGame


def analyze_drawing_with_ai(base64_str: str, word: str) -> Tuple[bool, str]:
    """
    Analyse le dessin avec Gemini 2.5 Flash via le nouveau SDK google-genai.
    """
    if not os.getenv("GEMINI_API_KEY"):
        return True, "Mode hors-ligne : On va dire que c'est de l'art abstrait, c'est validé !"

    try:
        # 1. Nettoyer le préfixe envoyé par le Frontend (ex: data:image/png;base64,...)
        if "," in base64_str:
            base64_str = base64_str.split(",")[1]

        # 2. Convertir le Base64 en bytes bruts (Plus besoin de la librairie PIL !)
        image_bytes = base64.b64decode(base64_str)

        # 3. Créer le prompt textuel
        prompt = (
            f"Tu es un critique d'art sarcastique et un peu ivre dans un bar. "
            f"Le joueur devait dessiner : '{word}'. Regarde ce dessin fait au doigt sur un téléphone.\n"
            f"Ligne 1 : Réponds STRICTEMENT par 'OUI' si on peut vaguement deviner que c'est ça (sois très indulgent), ou 'NON' si c'est n'importe quoi.\n"
            f"Ligne 2 : Fais un commentaire très drôle et piquant sur le chef-d'œuvre (1 phrase courte)."
        )

        # 4. Initialiser le client et créer le contenu avec types.Part
        client = genai.Client()
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[
                prompt,
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type='image/png'
                )
            ]
        )

        # 5. Parser la réponse
        lines = response.text.strip().split('\n')
        lines = [line.strip() for line in lines if line.strip()]

        is_valid = "OUI" in lines[0].upper()
        comment = lines[1] if len(lines) > 1 else "Je n'ai même pas les mots..."

        return is_valid, comment

    except Exception as e:
        print(f"[ERREUR IA] {e}")
        return False, "Le critique d'art s'est endormi sur le comptoir (Erreur API)."


class DrunkenDrawingGame(BaseMiniGame):
    @property
    def game_id(self) -> str:
        return "DRUNKEN_DRAWING"

    def setup_game(self, apero: Apero, db: Session) -> None:
        words_to_draw = [
            # 🍎 Objets Simples (Échauffement)
            "une banane", "un soleil", "une maison", "un cœur", "une fleur",
            "une lune", "une table", "un chapeau", "un parapluie", "une clé",
            "un sapin", "un nuage", "une épée", "un livre", "une tasse",

            # 🐱 Animaux (Souvent très drôles à voir)
            "un chat", "un chien", "un serpent", "un escargot", "un papillon",
            "une araignée", "un oiseau", "un poisson", "un éléphant", "une girafe",
            "un canard", "une tortue", "un dinosaure", "un requin", "une chauve-souris",

            # 🍻 Thème Apéro & Bar (Le cœur de BeerCall)
            "une chope de bière", "un cocktail", "une bouteille de vin", "un tire-bouchon",
            "une pinte", "un bretzel", "un kebab", "une tranche de pizza", "un burger",
            "un serveur", "un verre de shot", "une cacahuète", "une terrasse",

            # 🚀 Transport & Technologie
            "une voiture", "un avion", "un vélo", "une fusée", "un bateau",
            "un sous-marin", "un train", "un hélicoptère", "un smartphone", "un ordinateur",

            # 🎭 Émotions & Personnages
            "un visage souriant", "un bonhomme de neige", "un fantôme", "un pirate",
            "un ninja", "un extraterrestre", "un squelette", "un cow-boy", "un clown",
            "une couronne", "une main", "un pied",

            # 🏆 Défis "Expert" (Difficiles au doigt)
            "la Tour Eiffel", "une bicyclette de profil", "un instrument de musique",
            "une paire de lunettes", "un château fort", "une chaussure à lacets",
            "un appareil photo", "un gâteau d'anniversaire", "une ancre"
        ]

        state = dict(apero.current_game_state)
        state["word"] = random.choice(words_to_draw)
        state["drawing_base64"] = None
        state["ai_verdict"] = None
        state["ai_comment"] = None
        apero.current_game_state = state

    def get_sdui_payload(self, apero: Apero, db: Session) -> Dict[str, Any]:
        state = apero.current_game_state

        player_ids = state.get("player_ids", [])
        turn_index = state.get("turn_index", 0)
        current_player_id = player_ids[turn_index % len(player_ids)]

        current_username = next(
            (p.user.username for p in apero.participants if p.user_id == current_player_id),
            "Le Dessinateur"
        )

        # --- CAS 1 : L'IA A RENDU SON VERDICT ---
        if state.get("ai_verdict"):
            won = (state["ai_verdict"] == "WON")
            title = "C'est de l'Art !" if won else "Un véritable désastre..."
            style = "success" if won else "danger"
            punishment = "Distribue 2 gorgées !" if won else "Bois 3 gorgées de la honte !"

            return {
                "game_id": self.game_id,
                "turn_of": "Le Critique d'Art 🤖",
                "instruction_header": f"Verdict pour {current_username}",
                "title": title,
                "description": f"L'IA dit : \"{state.get('ai_comment')}\"\n\n{punishment}",
                "required_sensor": {
                    "type": "IMAGE_DISPLAY",
                    "image_data": state["drawing_base64"]
                },
                "actions": [
                    {"label": "On passe à la suite", "action_id": "NEXT_TURN", "style": style}
                ]
            }

        # --- CAS 2 : PHASE DE DESSIN ---
        return {
            "game_id": self.game_id,
            "turn_of": current_username,
            "instruction_header": "Picas-soûl 🎨",
            "title": "Dessin d'Ivrogne",
            "description": f"Tu as 15 secondes pour dessiner au doigt : {state['word']}",
            "required_sensor": {
                "type": "CANVAS_DRAW",
                "duration_ms": 15000,
                "stroke_color": "#D97706",
                "stroke_width": 6
            },
            "actions": []
        }

    def handle_action(self, apero: Apero, db: Session, action_payload: Dict[str, Any]) -> None:
        action_id = action_payload.get("action_id", "")
        state = dict(apero.current_game_state)

        if action_id == "DRAWING_FINISHED":
            image_b64 = action_payload.get("image_base64", "")
            state["drawing_base64"] = image_b64

            is_valid, comment = analyze_drawing_with_ai(image_b64, state["word"])

            state["ai_verdict"] = "WON" if is_valid else "LOST"
            state["ai_comment"] = comment

            apero.current_game_state = state

        elif action_id == "NEXT_TURN":
            state["turn_index"] = state.get("turn_index", 0) + 1
            apero.current_game_state = state
            apero.current_game_id = "TURN_TRANSITION"
