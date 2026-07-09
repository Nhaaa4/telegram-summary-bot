from __future__ import annotations

import random
from dataclasses import dataclass, field

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]


def _card_value(card: tuple[str, str]) -> int:
    rank = card[1]
    if rank in ("J", "Q", "K"):
        return 10
    if rank == "A":
        return 11
    return int(rank)


def _hand_value(hand: list[tuple[str, str]]) -> int:
    value = sum(_card_value(c) for c in hand)
    aces = sum(1 for c in hand if c[1] == "A")
    while value > 21 and aces:
        value -= 10
        aces -= 1
    return value


def _format_card(card: tuple[str, str]) -> str:
    return f"{card[1]}{card[0]}"


def _format_hand(hand: list[tuple[str, str]], hide_first: bool = False) -> str:
    if hide_first:
        return "?? " + " ".join(_format_card(c) for c in hand[1:])
    return " ".join(_format_card(c) for c in hand)


class Deck:
    def __init__(self) -> None:
        self.cards: list[tuple[str, str]] = [(s, r) for s in SUITS for r in RANKS]
        random.shuffle(self.cards)

    def draw(self) -> tuple[str, str]:
        return self.cards.pop()


@dataclass
class BlackjackGame:
    deck: Deck = field(default_factory=Deck)
    player_hand: list[tuple[str, str]] = field(default_factory=list)
    dealer_hand: list[tuple[str, str]] = field(default_factory=list)
    bet: int = 0
    state: str = "playing"

    def deal(self) -> None:
        self.player_hand = [self.deck.draw(), self.deck.draw()]
        self.dealer_hand = [self.deck.draw(), self.deck.draw()]
        if _hand_value(self.player_hand) == 21:
            self.state = "blackjack"

    def hit(self) -> str:
        self.player_hand.append(self.deck.draw())
        if _hand_value(self.player_hand) > 21:
            self.state = "player_bust"
        return self.state

    def stand(self) -> str:
        while _hand_value(self.dealer_hand) < 17:
            self.dealer_hand.append(self.deck.draw())
        pv = _hand_value(self.player_hand)
        dv = _hand_value(self.dealer_hand)
        if dv > 21:
            self.state = "dealer_bust"
        elif pv > dv:
            self.state = "player_win"
        elif pv < dv:
            self.state = "dealer_win"
        else:
            self.state = "push"
        return self.state

    def payout(self) -> int:
        if self.state == "blackjack":
            return int(self.bet * 1.5)
        if self.state in ("player_win", "dealer_bust"):
            return self.bet
        if self.state == "push":
            return 0
        return -self.bet

    @property
    def player_value(self) -> int:
        return _hand_value(self.player_hand)

    @property
    def dealer_value(self) -> int:
        return _hand_value(self.dealer_hand)

    @property
    def player_hand_str(self) -> str:
        return _format_hand(self.player_hand)

    @property
    def dealer_hand_str(self) -> str:
        return _format_hand(self.dealer_hand, hide_first=self.state == "playing")

    @property
    def dealer_hand_full(self) -> str:
        return _format_hand(self.dealer_hand)


def coinflip() -> str:
    return random.choice(["Heads", "Tails"])
