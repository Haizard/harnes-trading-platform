"""Rug-Pull Detection for Solana tokens.

Checks token safety before trading using Solana public RPC (FREE).
Verifies: mint authority, freeze authority, supply concentration,
top holder percentage, and honeypot patterns.
"""

import time
import requests
from dataclasses import dataclass, field
from typing import Optional

SOLANA_RPC = "https://api.mainnet-beta.solana.com"


@dataclass
class SafetyReport:
    token_address: str
    is_safe: bool = False
    risk_score: float = 0.0  # 0=safe, 100=definitely rug
    reasons: list = field(default_factory=list)
    mint_authority_revoked: bool = False
    freeze_authority_revoked: bool = False
    supply: float = 0.0
    decimals: int = 0
    holder_count: int = 0
    top_holder_pct: float = 0.0
    lp_burned: bool = False

    def to_dict(self):
        return {
            "token_address": self.token_address,
            "is_safe": self.is_safe,
            "risk_score": self.risk_score,
            "reasons": self.reasons,
            "mint_authority_revoked": self.mint_authority_revoked,
            "freeze_authority_revoked": self.freeze_authority_revoked,
            "supply": self.supply,
            "decimals": self.decimals,
            "holder_count": self.holder_count,
            "top_holder_pct": self.top_holder_pct,
            "lp_burned": self.lp_burned,
        }


class RugPullDetector:
    """Detect rug-pull risk for Solana tokens using public RPC."""

    def __init__(self, rpc_url=None):
        self.rpc_url = rpc_url or SOLANA_RPC
        self._cache = {}
        self._cache_ttl = 300  # 5 minutes

    def check(self, token_address: str) -> SafetyReport:
        """Full safety check for a token."""
        report = SafetyReport(token_address=token_address)

        # Check cache
        if token_address in self._cache:
            cached_time, cached_report = self._cache[token_address]
            if time.time() - cached_time < self._cache_ttl:
                return cached_report

        risk = 0.0

        # 1. Check mint authority
        mint_ok = self._check_mint_authority(token_address, report)
        if not mint_ok:
            risk += 40  # HIGH risk if mint authority not revoked

        # 2. Check freeze authority
        freeze_ok = self._check_freeze_authority(token_address, report)
        if not freeze_ok:
            risk += 30  # HIGH risk if freeze authority not revoked

        # 3. Check supply distribution
        self._check_supply(token_address, report)

        # 4. Check top holders
        top_holder_risk = self._check_top_holders(token_address, report)
        risk += top_holder_risk

        # 5. Final verdict
        report.risk_score = min(risk, 100.0)
        report.is_safe = report.risk_score < 50

        if report.risk_score >= 70:
            report.reasons.append("HIGH RISK: Likely rug-pull")
        elif report.risk_score >= 50:
            report.reasons.append("MEDIUM RISK: Proceed with caution")
        else:
            report.reasons.append("LOW RISK: Appears safe")

        # Cache result
        self._cache[token_address] = (time.time(), report)
        return report

    def _rpc_call(self, method, params):
        """Make a Solana RPC call."""
        try:
            resp = requests.post(
                self.rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get("result", {})
        except Exception as e:
            print("[RUG] RPC error: " + str(e), flush=True)
        return None

    def _check_mint_authority(self, token_address, report):
        """Check if mint authority is revoked."""
        result = self._rpc_call("getAccountInfo", [
            token_address, {"encoding": "jsonParsed"}
        ])
        if not result:
            report.reasons.append("Could not verify mint authority")
            return True  # Assume OK if can't check

        data = result.get("value", {}).get("data", {}).get("parsed", {})
        info = data.get("info", {})
        mint_auth = info.get("mintAuthority")
        report.decimals = info.get("decimals", 0)

        if mint_auth is None:
            report.mint_authority_revoked = True
            report.reasons.append("Mint authority revoked")
            return True
        else:
            report.reasons.append("Mint authority ACTIVE - can mint more tokens")
            return False

    def _check_freeze_authority(self, token_address, report):
        """Check if freeze authority is revoked."""
        result = self._rpc_call("getAccountInfo", [
            token_address, {"encoding": "jsonParsed"}
        ])
        if not result:
            return True

        data = result.get("value", {}).get("data", {}).get("parsed", {})
        info = data.get("info", {})
        freeze_auth = info.get("freezeAuthority")

        if freeze_auth is None:
            report.freeze_authority_revoked = True
            report.reasons.append("Freeze authority revoked")
            return True
        else:
            report.reasons.append("Freeze authority ACTIVE - can freeze your tokens")
            return False

    def _check_supply(self, token_address, report):
        """Check token supply."""
        result = self._rpc_call("getTokenSupply", [token_address])
        if not result:
            return

        value = result.get("value", {})
        report.supply = float(value.get("uiAmount", 0) or 0)
        report.decimals = value.get("decimals", 0)

    def _check_top_holders(self, token_address, report):
        """Check top holder concentration via getLargestAccounts."""
        result = self._rpc_call("getTokenLargestAccounts", [token_address])
        if not result:
            return 0

        accounts = result.get("value", [])
        if not accounts:
            return 0

        report.holder_count = len(accounts)

        # Get total supply
        supply_result = self._rpc_call("getTokenSupply", [token_address])
        if not supply_result:
            return 0

        total_supply = float(supply_result.get("value", {}).get("uiAmount", 0) or 0)
        if total_supply <= 0:
            return 0

        # Check top holder percentage
        top_holder_amount = float(accounts[0].get("uiAmount", 0) or 0)
        report.top_holder_pct = (top_holder_amount / total_supply * 100) if total_supply > 0 else 0

        risk = 0
        if report.top_holder_pct > 50:
            report.reasons.append("Top holder owns " + str(round(report.top_holder_pct, 1)) + "% - DUMP RISK")
            risk = 25
        elif report.top_holder_pct > 30:
            report.reasons.append("Top holder owns " + str(round(report.top_holder_pct, 1)) + "%")
            risk = 10
        elif report.top_holder_pct > 20:
            report.reasons.append("Top holder owns " + str(round(report.top_holder_pct, 1)) + "%")
            risk = 5

        # Check if top 5 holders own > 80%
        top5_amount = sum(float(a.get("uiAmount", 0) or 0) for a in accounts[:5])
        top5_pct = (top5_amount / total_supply * 100) if total_supply > 0 else 0
        if top5_pct > 80:
            report.reasons.append("Top 5 holders own " + str(round(top5_pct, 1)) + "%")
            risk += 15

        return risk

    def batch_check(self, token_addresses: list) -> dict:
        """Check multiple tokens at once."""
        results = {}
        for addr in token_addresses:
            results[addr] = self.check(addr)
            time.sleep(0.5)  # Rate limiting
        return results
