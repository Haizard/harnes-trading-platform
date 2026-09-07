"""Sequence-aware Binance order-book reconstruction primitives."""

from decimal import Decimal


class OrderBookGapError(RuntimeError):
    """Raised when an update cannot be applied without losing book continuity."""


class BinanceOrderBook:
    def __init__(self, last_update_id: int, bids: list, asks: list):
        self.last_update_id = int(last_update_id)
        self.bids = self._levels(bids)
        self.asks = self._levels(asks)

    @staticmethod
    def _levels(levels: list) -> dict[Decimal, Decimal]:
        return {Decimal(str(price)): Decimal(str(quantity)) for price, quantity in levels if Decimal(str(quantity)) > 0}

    def apply_update(self, first_update_id: int, final_update_id: int, bids: list, asks: list) -> None:
        first_update_id = int(first_update_id)
        final_update_id = int(final_update_id)
        if final_update_id <= self.last_update_id:
            return
        if first_update_id > self.last_update_id + 1 or final_update_id < self.last_update_id + 1:
            raise OrderBookGapError(
                f"depth gap: expected {self.last_update_id + 1}, received {first_update_id}-{final_update_id}"
            )

        self._apply_side(self.bids, bids)
        self._apply_side(self.asks, asks)
        self.last_update_id = final_update_id

    @staticmethod
    def _apply_side(book: dict[Decimal, Decimal], updates: list) -> None:
        for price, quantity in updates:
            price_decimal = Decimal(str(price))
            quantity_decimal = Decimal(str(quantity))
            if quantity_decimal == 0:
                book.pop(price_decimal, None)
            else:
                book[price_decimal] = quantity_decimal

    def top_levels(self, depth: int = 10) -> dict:
        return {
            "bids": sorted(self.bids.items(), reverse=True)[:depth],
            "asks": sorted(self.asks.items())[:depth],
            "last_update_id": self.last_update_id,
        }
