"""Tests for DomainManager — domain lifecycle control."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

async def dummy_task_fn():
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass

class TestDomainManager(unittest.IsolatedAsyncioTestCase):
    """5 tests for DomainManager as specified in TAREA 3."""

    def _make_manager(self):
        from services.domain_manager import DomainManager
        return DomainManager()

    async def test_start_domain_stocks_returns_true_when_not_running(self):
        """start_domain('stocks') returns (True, 'started') when not running."""
        manager = self._make_manager()

        mock_bot = MagicMock()
        mock_bot.run_forever = AsyncMock(return_value=None)

        async def fake_create(self_inner):
            task = asyncio.create_task(dummy_task_fn(), name="domain-stocks")
            return task, mock_bot

        with patch.object(manager, '_create_stocks_task', new=lambda: fake_create(manager)):
            # Patch _create_stocks_task to avoid real DB/client deps
            async def patched_create():
                task = asyncio.create_task(dummy_task_fn(), name="domain-stocks")
                return task, mock_bot

            manager._create_stocks_task = patched_create

            with patch('services.domain_manager.domain_manager', manager):
                started, msg = await manager.start_domain("stocks")

        assert started is True, f"Expected True got {started}"
        assert msg == "started"
        # Cleanup
        for t in manager._tasks.values():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

    async def test_start_domain_returns_false_if_already_running(self):
        """start_domain('stocks') returns (False, 'already_running') if running."""
        manager = self._make_manager()
        # Pre-inject a running task
        fake_task = asyncio.create_task(dummy_task_fn())
        manager._tasks["stocks"] = fake_task

        started, msg = await manager.start_domain("stocks")

        assert started is False
        assert msg == "already_running"
        fake_task.cancel()
        try:
            await fake_task
        except asyncio.CancelledError:
            pass

    async def test_stop_domain_cancels_task_and_returns_true(self):
        """stop_domain('stocks') cancels the task and returns (True, 'stopped')."""
        manager = self._make_manager()

        mock_bot = MagicMock()
        mock_bot.stop = MagicMock()

        fake_task = asyncio.create_task(dummy_task_fn())
        manager._tasks["stocks"] = fake_task
        manager._bots["stocks"] = mock_bot

        with patch('services.stocks_runtime.set_stocks_bot', MagicMock()):
            stopped, msg = await manager.stop_domain("stocks")

        assert stopped is True
        assert msg == "stopped"
        assert fake_task.cancelled() or fake_task.done()

    async def test_stop_domain_returns_false_if_not_running(self):
        """stop_domain('crypto') returns (False, 'not_running') when no task exists."""
        manager = self._make_manager()
        stopped, msg = await manager.stop_domain("crypto")
        assert stopped is False
        assert msg == "not_running"

    def test_get_status_returns_running_when_task_active(self):
        """get_status() returns 'running' when task is active."""
        manager = self._make_manager()

        async def _run():
            fake_task = asyncio.create_task(dummy_task_fn())
            manager._tasks["stocks"] = fake_task
            status = manager.get_status("stocks")
            fake_task.cancel()
            try:
                await fake_task
            except asyncio.CancelledError:
                pass
            return status

        status = asyncio.run(_run())
        assert status == "running"

    def test_get_status_returns_stopped_when_no_task(self):
        """get_status() returns 'stopped' when no task registered."""
        manager = self._make_manager()
        assert manager.get_status("stocks") == "stopped"


class TestDomainManagerFileExists(unittest.TestCase):
    """Smoke test — module can be imported."""

    def test_module_importable(self):
        from services.domain_manager import DomainManager, domain_manager
        assert DomainManager is not None
        assert domain_manager is not None
