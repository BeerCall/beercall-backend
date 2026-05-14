import random
from typing import Dict, Any

from sqlalchemy.orm import Session

from models.apero import Apero, ParticipationStatus
from .base import BaseMiniGame


class AvatarRussianRouletteGame(BaseMiniGame):
    @property
    def game_id(self) -> str:
        return "AVATAR_ROULETTE"

    def setup_game(self, apero: Apero, db: Session) -> None:
        questions = [
            # 🍻 Thème Soirée & Alcool
            "finir sous la table avant minuit",
            "vomir dans le Uber en rentrant",
            "s'endormir sur les toilettes du bar",
            "se faire virer par le videur ce soir",
            "payer une tournée générale sur un coup de tête",
            "perdre son téléphone, sa CB ou ses clés ce soir",
            "voler le verre de quelqu'un d'autre sans pression",
            "manger un kebab par terre à 4h du mat'",
            "inventer une fausse vie pour impressionner un inconnu au bar",
            "boire dans le verre des autres pour finir les fonds",

            # 📱 Thème Dossiers & Téléphone
            "envoyer un message gênant à son ex ce soir",
            "liker par erreur une vieille photo Insta de son crush de 2018",
            "avoir le pire historique de recherche Google",
            "se faire bloquer par quelqu'un sur les réseaux ce soir",
            "avoir le temps d'écran le plus honteux de la semaine",

            # 💔 Thème Amour & Drague
            "draguer lourdement le ou la barman",
            "retourner avec son ex pour la 5ème fois",
            "finir la soirée avec un(e) parfait(e) inconnu(e)",
            "se marier à Las Vegas sur un coup de tête",
            "tomber amoureux(se) d'une personne rencontrée il y a 10 minutes",

            # 🤡 Thème Absurde & Potes
            "finir en garde à vue pour une connerie monumentale",
            "rejoindre une secte louche sans s'en rendre compte",
            "devenir millionnaire grâce à une idée complètement débile",
            "pleurer devant un film d'animation pour enfants",
            "se battre avec un pigeon dans la rue pour un bout de pain",
            "survivre le plus longtemps à une apocalypse zombie",
            "oublier le prénom de la personne avec qui il/elle parle depuis une heure",
            "casser un objet de valeur chez l'hôte de la soirée",
            "se faire arnaquer de 1000€ par un faux prince nigérian",
            "ruiner l'ambiance avec une blague vraiment très malaisante"
        ]

        state = dict(apero.current_game_state)
        state["question"] = random.choice(questions)
        apero.current_game_state = state

    def get_sdui_payload(self, apero: Apero, db: Session) -> Dict[str, Any]:
        participants = [p.user.username for p in apero.participants if p.status == ParticipationStatus.JOINED]
        return {
            "turn_of": "Tout le monde",
            "instruction_header": "Votez !",
            "title": "Roulette Russe 🎯",
            "description": f"Qui est le plus susceptible de {apero.current_game_state.get('question')} ?",
            "required_sensor": {"type": "BUTTONS"},
            "actions": [{"label": name, "action_id": f"TARGET_{name}", "style": "primary"} for name in participants]
        }

    def handle_action(self, apero: Apero, db: Session, action_payload: Dict[str, Any]) -> None:
        action_id = action_payload.get("action_id", "")
        if action_id.startswith("TARGET_"):
            # LE JEU EST FINI :
            # 1. On passe à l'index du JOUEUR SUIVANT
            state = dict(apero.current_game_state)
            state["turn_index"] = state.get("turn_index", 0) + 1
            apero.current_game_state = state

            # 2. On retourne à la transition pour annoncer le nouveau porteur du téléphone
            apero.current_game_id = "TURN_TRANSITION"
