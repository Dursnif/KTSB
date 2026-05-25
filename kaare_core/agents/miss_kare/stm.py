"""
Miss Kåre – korttidsminne.
Lagrer filtrerte observasjoner per bruker: bare det som berørte Miss Kåre.
"""

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

MAX_PER_USER = 20


@dataclass
class Observation:
    timestamp: float
    user_msg: str
    kare_response: str
    miss_kare_comment: str  # [STILLE] eller selve kommentaren


class MissKareSTM:
    def __init__(self):
        self._obs: dict[str, deque] = defaultdict(lambda: deque(maxlen=MAX_PER_USER))

    def add(self, user_id: str, user_msg: str, kare_response: str, miss_kare_comment: str):
        self._obs[user_id].append(Observation(
            timestamp=time.time(),
            user_msg=user_msg,
            kare_response=kare_response,
            miss_kare_comment=miss_kare_comment,
        ))

    def get_recent(self, user_id: str, n: int = 5) -> list[Observation]:
        return list(self._obs[user_id])[-n:]

    def get_non_silent(self, user_id: str, n: int = 5) -> list[Observation]:
        all_obs = list(self._obs[user_id])
        return [o for o in all_obs if o.miss_kare_comment != "[STILLE]"][-n:]
