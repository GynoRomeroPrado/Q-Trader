"""Sentiment Oracle — Macroeconomic Risk Circuit Breaker (Cerebro 3)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

logger = logging.getLogger(__name__)


class SentimentOracle:
    """Asynchronous macro risk evaluator using CryptoPanic and VADER NLP.
    
    Acts as a Circuit Breaker inside the TradeExecutor. Ultra-lightweight CPU load.
    """

    def __init__(self, api_key: str = "", polling_interval: int = 60) -> None:
        self.api_key = api_key
        self.polling_interval = polling_interval
        
        # VADER: NLP optimizado para redes sociales/texto financiero corto. O(N) muy rápido sin RAM extrema.
        self.analyzer = SentimentIntensityAnalyzer()
        
        self._market_panic: bool = False
        self._panic_reason: str = ""
        
        # Palabras clave letales que ignoran el score VADER y disparan pánico O(1)
        self.lethal_keywords = {
            "hack", "hacked", "sec", "lawsuit", "delist", "delisting", 
            "bankrupt", "bankruptcy", "exploit", "crash", "subpoena", "fraud"
        }
        
        self._oracle_task: asyncio.Task | None = None
        self._is_running = False
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        """Inicia el background daemon de polling."""
        self._is_running = True
        self._session = aiohttp.ClientSession()
        self._oracle_task = asyncio.create_task(self._poll_news_loop())
        logger.info("🧠 Cerebro 3 (Sentiment Oracle) iniciado. Monitoreando riesgo macro en I/O.")

    async def stop(self) -> None:
        """Cierra conexiones de red y detiene el Oráculo de forma segura."""
        self._is_running = False
        if self._oracle_task:
            self._oracle_task.cancel()
            try:
                await self._oracle_task
            except asyncio.CancelledError:
                pass
        if self._session:
            await self._session.close()

    @property
    def panic_reason(self) -> str:
        return self._panic_reason

    def _evaluate_compound_score(self, titles: list[str]) -> float:
        """Calcula el sentimiento asimétrico compuesto usando VADER.
        Escala: -1 (Pánico extremo) a +1 (Euforia).
        """
        if not titles:
            return 0.0
        
        total_score = 0.0
        for title in titles:
            score = self.analyzer.polarity_scores(title)
            total_score += score["compound"]
            
        return total_score / len(titles)

    def _check_lethal_keywords(self, titles: list[str]) -> str | None:
        """Detector determinístico de catástrofes de mercado (O(N*M))."""
        for title in titles:
            # Tokenización súper básica para evitar pesadez de NLTK
            words = set(title.lower().replace(",", "").replace(".", "").split())
            intersection = self.lethal_keywords.intersection(words)
            if intersection:
                return f"Keyword letal '{intersection.pop()}' en: {title[:50]}..."
        return None

    async def _poll_news_loop(self) -> None:
        """Polling loop asíncrono para mantener fresca la matriz de riesgo macro."""
        # Endpoint público gratuito de CryptoPanic (requiere Auth Token en la URL)
        url = f"https://cryptopanic.com/api/v1/posts/?auth_token={self.api_key}&regions=en&public=true"

        while self._is_running:
            if not self.api_key:
                logger.warning("SentimentOracle: Sin API Key de CryptoPanic. Oráculo operando en modo simulación (SEGURO).")
                await asyncio.sleep(self.polling_interval)
                continue

            try:
                # I/O a la red (El procesador pasa a estado IDLE aquí)
                async with self._session.get(url, timeout=10) as response:
                    if response.status != 200:
                        logger.error(f"Oracle API error: Code {response.status}")
                        await asyncio.sleep(self.polling_interval)
                        continue
                    
                    data = await response.json()
                    
                # Extraemos y parseamos solo los 20 titulares más recientes (JSON Parsing)
                results = data.get("results", [])
                titles = [item["title"] for item in results[:20]]
                
                # 1. Filtro O(1) de Keywords Críticas
                lethal_hit = self._check_lethal_keywords(titles)
                if lethal_hit:
                    if not self._market_panic:
                        logger.error(f"🚨 CIRCUIT BREAKER ACTIVADO (Keyword): {lethal_hit}")
                    self._market_panic = True
                    self._panic_reason = lethal_hit
                else:
                    # 2. Análisis VADER NLP (Costo CPU muy bajo por estar vectorizado pre-reglas)
                    avg_sentiment = self._evaluate_compound_score(titles)
                    
                    # Umbral de Pánico Matemático (-0.6 representa negatividad fuerte y sostenida)
                    if avg_sentiment < -0.6:
                        if not self._market_panic:
                            logger.error(f"🚨 CIRCUIT BREAKER ACTIVADO (Sentimiento): {avg_sentiment:.2f}")
                        self._market_panic = True
                        self._panic_reason = f"VADER negativo contundente: {avg_sentiment:.2f}"
                    else:
                        if self._market_panic:
                            logger.info("🟢 Pánico disipado. Mercado macro seguro de nuevo.")
                        self._market_panic = False
                        self._panic_reason = ""
                        
            except asyncio.TimeoutError:
                logger.warning("Oracle Timeout: CryptoPanic inalcanzable. Manteniendo estado anterior.")
            except Exception as e:
                logger.error(f"Oracle Exception no controlada: {e}")
                
            # Cooldown asíncrono estricto para no consumir Rate Limits (60s)
            await asyncio.sleep(self.polling_interval)

    async def is_market_safe(self) -> bool:
        """Interface asíncrona para que el Cerebro 1 (Execution) verifique si puede operar."""
        return not self._market_panic
