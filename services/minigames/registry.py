from .barman import BarmanGame
from .base import BaseMiniGame
from .avatar_roulette import AvatarRussianRouletteGame
from .brain_duel import BrainDuelGame
from .death_finger import DeathFingerGame
from .drunken_drawing import DrunkenDrawingGame
from .hot_potato import HotPotatoGame
from .max_pressure import MaxPressureGame
from .penalty_shootout import PenaltyShootoutGame
from .photo_challenge import PhotoChallengeGame
from .transition import TurnTransitionGame

GAME_REGISTRY: dict[str, BaseMiniGame] = {
    "AVATAR_ROULETTE": AvatarRussianRouletteGame(),
    "TURN_TRANSITION": TurnTransitionGame(),
    "HOT_POTATO": HotPotatoGame(),
    "BRAIN_DUEL": BrainDuelGame(),
    "DEATH_FINGER": DeathFingerGame(),
    "BARMAN_EQUILIBRISTE": BarmanGame(),
    "MAX_PRESSURE": MaxPressureGame(),
    "PENALTY_SHOOTOUT": PenaltyShootoutGame(),
    "PHOTO_CHALLENGE": PhotoChallengeGame(),
    "DRUNKEN_DRAWING": DrunkenDrawingGame(),
}


def get_game_instance(game_id: str) -> BaseMiniGame | None:
    return GAME_REGISTRY.get(game_id)
