"""
Tests for all 13 remaining DSH features
"""

import pytest
import asyncio
import os
import numpy as np
from src.ensemble_strategy import EnsembleStrategy, Signal, StrategySignal
from src.async_scheduler import AsyncScheduler, JobStatus
from src.spill_storage import SpillStorage, SpillResult
from src.session_query import SessionQuery
from src.trade_planner import TradePlanner, PlanStatus, PlanStep
from src.mcp_registry import MCPRegistry, TradingMCPTool, ToolParameter, create_default_mcp_registry
from src.funding_costs import FundingCostTracker
from src.correlation_manager import CorrelationManager
from src.walk_forward import WalkForwardValidator, WalkForwardResult
from src.process_isolation import ProcessIsolationManager, ComponentStatus
from src.human_feedback import HumanFeedbackSystem


# ── Ensemble Strategy Tests ───────────────────────────────────

class TestEnsembleStrategy:
    @pytest.fixture
    def ensemble(self):
        e = EnsembleStrategy()
        e.register("momentum", lambda f: StrategySignal("momentum", Signal.BUY, 0.8), weight=1.5)
        e.register("mean_reversion", lambda f: StrategySignal("mean_reversion", Signal.HOLD, 0.5), weight=1.0)
        return e

    @pytest.mark.asyncio
    async def test_evaluate(self, ensemble):
        result = await ensemble.evaluate({})
        assert result.contributing_strategies == 2
        assert result.signal in (Signal.BUY, Signal.HOLD, Signal.SELL)

    @pytest.mark.asyncio
    async def test_set_weight(self, ensemble):
        ensemble.set_weight("momentum", 2.0)
        assert ensemble._weights["momentum"] == 2.0

    @pytest.mark.asyncio
    async def test_empty_ensemble(self):
        e = EnsembleStrategy()
        result = await e.evaluate({})
        assert result.signal == Signal.HOLD

    @pytest.mark.asyncio
    async def test_to_dict(self, ensemble):
        result = await ensemble.evaluate({})
        d = result.to_dict()
        assert 'signal' in d
        assert 'confidence' in d


# ── Async Scheduler Tests ─────────────────────────────────────

class TestAsyncScheduler:
    @pytest.mark.asyncio
    async def test_register_and_status(self):
        scheduler = AsyncScheduler()
        scheduler.register("test", lambda: None, interval_seconds=60)
        status = scheduler.get_status()
        assert "test" in status

    @pytest.mark.asyncio
    async def test_enable_disable(self):
        scheduler = AsyncScheduler()
        scheduler.register("test", lambda: None, 60)
        scheduler.disable("test")
        assert not scheduler._jobs["test"].enabled
        scheduler.enable("test")
        assert scheduler._jobs["test"].enabled

    @pytest.mark.asyncio
    async def test_run_now(self):
        scheduler = AsyncScheduler()
        scheduler.register("test", lambda: None, 60)
        result = await scheduler.run_now("test")
        assert result.status == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_run_now_error(self):
        def bad_job(): raise RuntimeError("fail")
        scheduler = AsyncScheduler()
        scheduler.register("bad", bad_job, 60)
        result = await scheduler.run_now("bad")
        assert result.status == JobStatus.FAILED


# ── Spill Storage Tests ───────────────────────────────────────

class TestSpillStorage:
    def test_small_data_not_spilled(self, tmp_path):
        storage = SpillStorage(spill_dir=str(tmp_path), max_preview_chars=1000)
        result = storage.spill({"key": "value"}, name="test")
        assert not result.truncated
        assert result.file_path is None

    def test_large_data_spilled(self, tmp_path):
        storage = SpillStorage(spill_dir=str(tmp_path), max_preview_chars=50)
        result = storage.spill({"key": "x" * 200}, name="test")
        assert result.truncated
        assert result.file_path is not None

    def test_compact_for_llm(self, tmp_path):
        storage = SpillStorage(spill_dir=str(tmp_path), max_preview_chars=100)
        text = storage.compact_for_llm({"data": "x" * 500})
        assert len(text) <= 200  # Bounded preview


# ── Session Query Tests ───────────────────────────────────────

class TestSessionQuery:
    @pytest.mark.asyncio
    async def test_empty_query(self):
        query = SessionQuery()
        results = await query.search()
        assert results == []

    @pytest.mark.asyncio
    async def test_get_performance(self):
        query = SessionQuery()
        perf = await query.get_performance()
        assert 'total_trades' in perf


# ── Trade Planner Tests ───────────────────────────────────────

class TestTradePlanner:
    def test_create_plan(self):
        planner = TradePlanner()
        plan = planner.create_plan("FART", "buy", entry_price=0.004, size_usd=25)
        assert plan.status == PlanStatus.DRAFT
        assert plan.token == "FART"

    def test_advance_plan(self):
        planner = TradePlanner()
        plan = planner.create_plan("FART", "buy")
        planner.advance(plan, note="Analysis complete")
        assert plan.current_step == PlanStep.VALIDATE
        assert len(plan.steps_completed) == 1

    def test_complete_plan(self):
        planner = TradePlanner()
        plan = planner.create_plan("FART", "buy")
        for _ in range(7):
            planner.advance(plan)
        assert plan.status == PlanStatus.COMPLETED

    def test_cancel_plan(self):
        planner = TradePlanner()
        plan = planner.create_plan("FART", "buy")
        planner.cancel(plan, "Changed mind")
        assert plan.status == PlanStatus.CANCELLED

    def test_active_plans(self):
        planner = TradePlanner()
        plan = planner.create_plan("FART", "buy")
        planner.advance(plan)
        assert len(planner.get_active_plans()) == 1


# ── MCP Registry Tests ────────────────────────────────────────

class TestMCPRegistry:
    def test_create_default(self):
        registry = create_default_mcp_registry()
        tools = registry.list_tool_names()
        assert len(tools) >= 10

    def test_list_tools(self):
        registry = create_default_mcp_registry()
        tools = registry.list_tools()
        assert len(tools) >= 10
        # Verify each tool has required fields
        for tool in tools:
            assert 'name' in tool
            assert 'description' in tool
            assert 'source' in tool
            assert 'parameters' in tool

    def test_get_tool(self):
        registry = create_default_mcp_registry()
        tool = registry.get_tool("get_token_price")
        assert tool is not None
        assert tool.name == "get_token_price"
        assert tool.source == "jupiter"

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        registry = create_default_mcp_registry()
        result = await registry.call_tool("nonexistent_tool")
        assert result.success is False
        assert "Unknown tool" in result.error

    @pytest.mark.asyncio
    async def test_call_tool(self):
        registry = create_default_mcp_registry()
        result = await registry.call_tool("get_portfolio_state")
        assert result.success is True
        assert result.data is not None
        assert result.source == "paper_trader"

    @pytest.mark.asyncio
    async def test_call_history(self):
        registry = create_default_mcp_registry()
        await registry.call_tool("get_risk_state")
        history = registry.get_call_history()
        assert len(history) >= 1
        assert history[0]["tool"] == "get_risk_state"

    def test_tool_parameters(self):
        registry = create_default_mcp_registry()
        tool = registry.get_tool("get_token_price")
        assert len(tool.parameters) == 1
        assert tool.parameters[0].name == "token_address"
        assert tool.parameters[0].required is True
        assert tool.parameters[0].type == "string"

    def test_wallet_intelligence_tools_exist(self):
        """Verify wallet intelligence MCP tools are registered."""
        registry = create_default_mcp_registry()
        wallet_tools = [t for t in registry.list_tool_names() if 'wallet' in t or 'smart_money' in t]
        assert len(wallet_tools) >= 3
        assert "get_wallet_activity" in registry.list_tool_names()
        assert "get_wallet_score" in registry.list_tool_names()
        assert "get_smart_money_flow" in registry.list_tool_names()


# ── Wallet Tracker Tests ──────────────────────────────────────
class TestWalletTracker:
    def test_create_tracker(self):
        from src.wallet_tracker import WalletTracker
        tracker = WalletTracker()
        assert tracker is not None
        stats = tracker.get_stats()
        assert "tracked_wallets" in stats
        assert "events_24h" in stats

    def test_add_wallet(self):
        from src.wallet_tracker import WalletTracker
        tracker = WalletTracker()
        test_addr = "TestWallet11111111111111111111111111111111"
        tracker.add_wallet(test_addr, label="test_wallet", tags=["test"])
        wallets = tracker.get_tracked_wallets()
        assert any(w["address"] == test_addr for w in wallets)
        # Cleanup
        tracker.remove_wallet(test_addr)

    def test_wallet_activity_log(self):
        from src.wallet_tracker import WalletTracker
        tracker = WalletTracker()
        activity = tracker.get_recent_activity(hours=24)
        assert isinstance(activity, list)

    def test_token_activity(self):
        from src.wallet_tracker import WalletTracker
        tracker = WalletTracker()
        activity = tracker.get_token_activity("FakeTokenAddress1111111111111111")
        assert isinstance(activity, list)


class TestWalletScorer:
    def test_create_scorer(self):
        from src.wallet_scorer import WalletScorer
        scorer = WalletScorer()
        assert scorer is not None
        stats = scorer.get_stats()
        assert "total_scored" in stats

    def test_score_wallet_no_data(self):
        from src.wallet_scorer import WalletScorer
        scorer = WalletScorer()
        score = scorer.score_wallet("FakeWallet1111111111111111111111111111")
        # Should return None for wallet with no activity
        assert score is None

    def test_get_top_wallets(self):
        from src.wallet_scorer import WalletScorer
        scorer = WalletScorer()
        top = scorer.get_top_wallets(limit=5)
        assert isinstance(top, list)


class TestSmartMoneyDetector:
    def test_create_detector(self):
        from src.smart_money_detector import SmartMoneyDetector
        detector = SmartMoneyDetector()
        assert detector is not None
        stats = detector.get_stats()
        assert "total_signals" in stats

    def test_scan_no_data(self):
        from src.smart_money_detector import SmartMoneyDetector
        detector = SmartMoneyDetector()
        signals = detector.scan(hours=1)
        assert isinstance(signals, list)

    def test_get_active_consensus(self):
        from src.smart_money_detector import SmartMoneyDetector
        detector = SmartMoneyDetector()
        consensus = detector.get_active_consensus()
        assert isinstance(consensus, list)


# ── Funding Cost Tests ────────────────────────────────────────
class TestFundingCosts:
    def test_record_cost(self):
        tracker = FundingCostTracker()
        tracker.record_cost("BTCUSDT", 1000, rate=0.01)
        assert len(tracker._records) == 1

    def test_total_cost(self):
        tracker = FundingCostTracker()
        tracker.record_cost("BTCUSDT", 1000, rate=0.01)
        tracker.record_cost("BTCUSDT", 1000, rate=0.01)
        total = tracker.get_total_cost()
        assert total > 0

    def test_by_symbol(self):
        tracker = FundingCostTracker()
        tracker.record_cost("BTCUSDT", 1000, rate=0.01)
        tracker.record_cost("ETHUSDT", 500, rate=0.005)
        by_sym = tracker.get_cost_by_symbol()
        assert "BTCUSDT" in by_sym
        assert "ETHUSDT" in by_sym

    def test_report(self):
        tracker = FundingCostTracker()
        tracker.record_cost("BTCUSDT", 1000, rate=0.01)
        report = tracker.get_report()
        assert 'total_cost_usd' in report


# ── Correlation Manager Tests ─────────────────────────────────

class TestCorrelationManager:
    def test_update_price(self):
        cm = CorrelationManager()
        for p in [100, 101, 102, 103, 104]:
            cm.update_price("A", p)
        assert len(cm._price_history["A"]) == 5

    def test_calculate_correlation(self):
        cm = CorrelationManager()
        prices = [100 + i * 0.5 + np.random.randn() * 0.1 for i in range(50)]
        for p in prices:
            cm.update_price("A", p)
            cm.update_price("B", p * 1.1)  # Highly correlated
        result = cm.calculate_correlation("A", "B")
        assert result is not None
        assert result.correlation > 0.5

    def test_portfolio_risk(self):
        cm = CorrelationManager()
        for i in range(50):
            cm.update_price("A", 100 + i)
            cm.update_price("B", 200 + i * 2)
        risk = cm.get_portfolio_risk()
        assert 'risk_level' in risk


# ── Walk-Forward Tests ────────────────────────────────────────

class TestWalkForward:
    @pytest.mark.asyncio
    async def test_validate(self):
        validator = WalkForwardValidator(train_days=20, test_days=5)
        prices = [100 + i * 0.5 + np.random.randn() * 2 for i in range(100)]

        async def dummy_strategy(prices):
            return "BUY"

        result = await validator.validate(dummy_strategy, "TEST", prices)
        assert isinstance(result, WalkForwardResult)
        assert result.periods_tested > 0

    @pytest.mark.asyncio
    async def test_insufficient_data(self):
        validator = WalkForwardValidator(train_days=100, test_days=50)
        async def dummy(p): return "HOLD"
        result = await validator.validate(dummy, "TEST", [1, 2, 3])
        assert not result.deployable


# ── Process Isolation Tests ───────────────────────────────────

class TestProcessIsolation:
    def test_register(self):
        mgr = ProcessIsolationManager()
        mgr.register("test", lambda: None)
        assert "test" in mgr._components

    def test_stop_all(self):
        mgr = ProcessIsolationManager()
        mgr.register("a", lambda: None)
        mgr.register("b", lambda: None)
        mgr.stop_all()
        for c in mgr._components.values():
            assert c.status == ComponentStatus.STOPPED

    def test_get_status(self):
        mgr = ProcessIsolationManager()
        mgr.register("test", lambda: None)
        status = mgr.get_status()
        assert "test" in status


# ── Human Feedback Tests ──────────────────────────────────────

class TestHumanFeedback:
    def test_record_feedback(self, tmp_path):
        system = HumanFeedbackSystem(history_dir=str(tmp_path))
        system.record_feedback("trade_1", rating=4, category="entry", comment="Good entry")
        feedback = system.get_feedback()
        assert len(feedback) == 1

    def test_filter_by_category(self, tmp_path):
        system = HumanFeedbackSystem(history_dir=str(tmp_path))
        system.record_feedback("t1", rating=4, category="entry")
        system.record_feedback("t2", rating=3, category="exit")
        entry_feedback = system.get_feedback(category="entry")
        assert len(entry_feedback) == 1

    def test_summary(self, tmp_path):
        system = HumanFeedbackSystem(history_dir=str(tmp_path))
        system.record_feedback("t1", rating=5, category="entry")
        system.record_feedback("t2", rating=3, category="exit")
        summary = system.get_summary()
        assert summary['total'] == 2
        assert summary['avg_rating'] == 4.0

    def test_rating_clamp(self, tmp_path):
        system = HumanFeedbackSystem(history_dir=str(tmp_path))
        system.record_feedback("t1", rating=10)  # Should clamp to 5
        system.record_feedback("t2", rating=-5)  # Should clamp to 1
        feedback = system.get_feedback()
        assert feedback[0]['rating'] == 5
        assert feedback[1]['rating'] == 1
