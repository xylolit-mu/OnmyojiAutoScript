"""Runtime adapters for the active rule-based draft and lineup policy."""
from tasks.DraftDuel.strategy import lineup


def choose_pick(owner, offers, team=()):
    return lineup.choose_pick(offers, team=team)


def plan_lineup(owner, available, *, team_size=3, locked=(), field_weights=None):
    return lineup.plan_lineup(available, team_size=team_size, locked=locked,
                              field_weights=field_weights)


def plan_duel_lineup(owner, own, mate, *, locked=(), mate_locked=(), field_weights=None):
    return lineup.plan_duel_lineup(own, mate, locked=locked, mate_locked=mate_locked,
                                   field_weights=field_weights)
