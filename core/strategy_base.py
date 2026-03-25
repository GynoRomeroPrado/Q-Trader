"""Microstructure Strategy Base & Order Book Imbalance (OBI)."""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)


class Signal(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class OrderBookStrategy:
    """Microestructura HFT basada en Imbalance del L2 (Order Book Imbalance)."""

    def __init__(self, depth: int = 10, imbalance_threshold: float = 0.65) -> None:
        self.depth = depth
        self.thresh = imbalance_threshold

    @property
    def name(self) -> str:
        return f"OBI-HFT(Depth={self.depth}, Thresh={self.thresh})"

    def process_orderbook(self, ob: Dict[str, Any]) -> Tuple[Signal, float]:
        """Analiza el L2 determinísticamente O(1).
        
        Retorna:
            (Señal, Proxy_ATR)
            Proxy_ATR = (Best_Ask - Best_Bid) / Best_Bid (Spread nominal en bps)
        """
        bids = ob.get("bids", [])
        asks = ob.get("asks", [])

        if len(bids) < self.depth or len(asks) < self.depth:
            return Signal.HOLD, 0.0

        # Volumen acumulado en el nivel de profundidad N
        bid_vol = sum(vol for _, vol in bids[:self.depth])
        ask_vol = sum(vol for _, vol in asks[:self.depth])
        total_vol = bid_vol + ask_vol

        if total_vol == 0:
            return Signal.HOLD, 0.0

        # Order Book Imbalance (OBI) estandarizado [-1, 1]
        imbalance = (bid_vol - ask_vol) / total_vol

        # Volatilidad Proxy (Micro-Spread)
        best_bid, best_ask = bids[0][0], asks[0][0]
        atr_proxy = (best_ask - best_bid) / best_bid

        # Generación de Señal en extremos de liquidez asimétrica
        if imbalance > self.thresh:
            return Signal.BUY, atr_proxy
        elif imbalance < -self.thresh:
            return Signal.SELL, atr_proxy

        return Signal.HOLD, atr_proxy
