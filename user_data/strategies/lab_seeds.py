"""Research only: the lab's random-entry control with other random sequences, to see how wide luck is."""
from lab import LabCoinFlip


class LabCoinFlipB(LabCoinFlip):
    SALT = "b"


class LabCoinFlipC(LabCoinFlip):
    SALT = "c"


class LabCoinFlipD(LabCoinFlip):
    SALT = "d"


class LabCoinFlipE(LabCoinFlip):
    SALT = "e"
