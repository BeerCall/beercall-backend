import random
import time
from typing import Dict, Any
from sqlalchemy.orm import Session
from models.apero import Apero
from .base import BaseMiniGame


class HotPotatoGame(BaseMiniGame):
    @property
    def game_id(self) -> str:
        return "HOT_POTATO"

    def setup_game(self, apero: Apero, db: Session) -> None:
        themes = [
            # 🍻 Thème Apéro & Soirée
            "Marques de bière",
            "Noms de cocktails",
            "Choses qu'on trouve dans un bar",
            "Marques de vodka, rhum ou tequila",
            "Choses qu'on mange en rentrant de soirée à 4h",
            "Excuses pour fuir un date Tinder éclaté",
            "Jeux à boire classiques",
            "Sons honteux qu'on chante en fin de soirée",
            "Choses qu'on perd tout le temps en soirée",

            # 🤡 Thème Fun & Décalé
            "Célébrités chauves",
            "Insultes originales ou régionales",
            "Choses qu'on peut dire au lit ET à table",
            "Prénoms de grands-parents",
            "Objets suspects à trouver sous un lit",
            "Mots qui riment avec 'Bière'",
            "Super-pouvoirs complètement inutiles",
            "Choses qu'on cache quand on a des invités",
            "Pires cadeaux de Noël à recevoir",
            "Moyens de se faire virer en 24h",

            # 🍿 Thème Pop Culture
            "Noms de Pokémon de la première génération",
            "Personnages de Harry Potter",
            "Rappeurs ou rappeuses francophones",
            "Films d'animation Disney ou Pixar",
            "Acteurs qui ont joué un super-héros (Marvel/DC)",
            "Séries Netflix que tout le monde a vues",
            "Jeux vidéo qui ruinent des amitiés (ex: Mario Kart)",
            "Émissions de télé-réalité",

            # 🧠 Thème "Le cerveau bugue sous la pression"
            "Pays d'Europe",
            "Animaux finissant par la lettre A",
            "Marques de voitures de luxe",
            "Capitales commençant par une consonne",
            "Fruits et légumes verts",
            "Sports qui se jouent SANS ballon",
            "Mots qui finissent en '-tion'",
            "Métiers qui demandent de porter un uniforme",
            "Instruments de musique à cordes",
            "Marques de fast-food",
            "Choses qui sont naturellement rouges",
            "Animaux qui vivent sous l'eau",
            "Objets qui fonctionnent à piles"
        ]

        state = dict(apero.current_game_state)
        state["theme"] = random.choice(themes)

        # On définit l'explosion entre 15 et 35 secondes dans le futur
        duration = random.randint(15, 35)
        state["explode_at"] = time.time() + duration

        # Le premier qui a la bombe est celui dont c'est le tour
        state["holder_index"] = state.get("turn_index", 0)

        # Indicateur pour savoir si la bombe a pété
        state["is_exploded"] = False

        apero.current_game_state = state

    def get_sdui_payload(self, apero: Apero, db: Session) -> Dict[str, Any]:
        state = apero.current_game_state
        player_ids = state.get("player_ids", [])

        # On trouve qui tient la bombe actuellement
        holder_id = player_ids[state["holder_index"] % len(player_ids)]
        holder_name = next((p.user.username for p in apero.participants if p.user_id == holder_id), "Inconnu")

        # --- CAS 1 : LA BOMBE A EXPLOSÉ ---
        if state.get("is_exploded", False):
            return {
                "turn_of": "BOOM 💥",
                "instruction_header": "Trop tard !",
                "title": f"Désolé {holder_name}...",
                "description": "La bombe a explosé dans tes mains. Tu dois boire 3 gorgées !",
                "required_sensor": {"type": "BUTTONS"},
                "actions": [
                    {"label": "J'assume et on continue 🍻", "action_id": "FINISH_GAME", "style": "danger"}
                ]
            }

        # --- CAS 2 : LA BOMBE TOURNE ENCORE ---
        remaining_time = max(0, state["explode_at"] - time.time())

        return {
            "turn_of": holder_name,
            "instruction_header": f"La bombe est sur {holder_name} 💣",
            "title": state["theme"],
            "description": "Donne une réponse, tape sur l'écran et passe le téléphone !",
            "required_sensor": {
                "type": "TIME_BOMB_BUTTON",
                "remaining_ms": int(remaining_time * 1000)  # Le front a besoin de savoir quand sonner
            },
            "actions": [
                # Pas de boutons standards ici, c'est le composant TIME_BOMB qui gère l'écran tactile
            ]
        }

    def handle_action(self, apero: Apero, db: Session, action_payload: Dict[str, Any]) -> None:
        action_id = action_payload.get("action_id", "")
        state = dict(apero.current_game_state)

        if action_id == "BOMB_PASSED":
            # Le joueur a tapé l'écran ! On vérifie si c'était à temps.
            if time.time() > state["explode_at"]:
                state["is_exploded"] = True
            else:
                # Ouf ! On passe le téléphone au joueur suivant
                state["holder_index"] += 1
            apero.current_game_state = state

        elif action_id == "BOMB_EXPLODED":
            # Le timer du frontend est arrivé à zéro
            state["is_exploded"] = True
            apero.current_game_state = state

        elif action_id == "FINISH_GAME":
            # Fin du jeu, on passe au vrai tour suivant et on retourne à la transition
            state["turn_index"] = state.get("turn_index", 0) + 1
            apero.current_game_state = state
            apero.current_game_id = "TURN_TRANSITION"
