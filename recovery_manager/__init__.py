"""Recovery Manager package (Project 4).

Owns Write-Ahead Logging and the three-phase (Analysis / Redo / Undo) ARIES
crash-recovery algorithm. See recovery_manager/manager.py.
"""

from .manager import RecoveryManager

__all__ = ["RecoveryManager"]
