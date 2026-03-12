#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AAIXII — AngelaAIX clawbot companion app.
Interact with the AngelaAIX contract: submit claws, query state, and manage operator/guardian flows.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    from web3 import Web3
    from eth_account import Account
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    Web3 = None
    Account = None

# -----------------------------------------------------------------------------
# Constants — aligned with AngelaAIX.sol
# -----------------------------------------------------------------------------

APP_NAME = "AAIXII"
APP_VERSION = "1.0.0"
AAIX_VERSION = 3
MAX_CLAW_KIND = 12
MAX_CLAWS_PER_BATCH = 32
MIN_COOLDOWN_BLOCKS = 2
MAX_COOLDOWN_BLOCKS = 1000
MIN_WINDOW_BLOCKS = 10
MAX_WINDOW_BLOCKS = 500
AAIX_DOMAIN_STR = "AngelaAIX.Claw.v3"

CLAW_KIND_NAMES = [
    "", "swap", "batch", "signal", "harvest", "rebalance",
    "exit", "enter", "custom_a", "custom_b", "custom_c", "emergency", "passthrough",
]

DEFAULT_RPC = "https://eth.llamarpc.com"
DEFAULT_RPC_SEPOLIA = "https://rpc.sepolia.org"
DEFAULT_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".aaixii")
DEFAULT_CONFIG_FILE = "config.json"

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

def setup_logging(level: str = "INFO", log_file: Optional[str] = None) -> None:
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=getattr(logging, level.upper()), format=fmt, handlers=handlers)

LOG = logging.getLogger("aaixii")

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

@dataclass
class ChainConfig:
    chain_id: int
    rpc_url: str
    name: str

CHAINS = {
    "mainnet": ChainConfig(1, DEFAULT_RPC, "Ethereum Mainnet"),
    "sepolia": ChainConfig(11155111, DEFAULT_RPC_SEPOLIA, "Sepolia"),
}

@dataclass
class AAIXIIConfig:
    chain: str = "sepolia"
    rpc_url: Optional[str] = None
    contract_address: Optional[str] = None
    private_key: Optional[str] = None
    config_dir: str = DEFAULT_CONFIG_DIR
    gas_limit_submit: int = 250_000
    gas_limit_batch: int = 600_000
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def effective_rpc(self) -> str:
        return self.rpc_url or CHAINS.get(self.chain, CHAINS["sepolia"]).rpc_url

    @property
    def chain_id(self) -> int:
        return CHAINS.get(self.chain, CHAINS["sepolia"]).chain_id

    def config_path(self, name: str) -> str:
        return os.path.join(self.config_dir, name)

    def ensure_config_dir(self) -> None:
        Path(self.config_dir).mkdir(parents=True, exist_ok=True)

    def save(self, path: Optional[str] = None) -> None:
        self.ensure_config_dir()
        p = path or self.config_path(DEFAULT_CONFIG_FILE)
        data = {
            "chain": self.chain,
            "rpc_url": self.rpc_url,
            "contract_address": self.contract_address,
            "gas_limit_submit": self.gas_limit_submit,
            "gas_limit_batch": self.gas_limit_batch,
            **self.extra,
        }
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "AAIXIIConfig":
        config_dir = path or os.path.join(DEFAULT_CONFIG_DIR, DEFAULT_CONFIG_FILE)
        if os.path.isfile(config_dir):
            with open(config_dir, encoding="utf-8") as f:
                data = json.load(f)
            return cls(
                chain=data.get("chain", "sepolia"),
                rpc_url=data.get("rpc_url"),
                contract_address=data.get("contract_address"),
                private_key=data.get("private_key"),
                config_dir=os.path.dirname(config_dir) or DEFAULT_CONFIG_DIR,
                gas_limit_submit=data.get("gas_limit_submit", 250_000),
                gas_limit_batch=data.get("gas_limit_batch", 600_000),
                extra={k: v for k, v in data.items() if k not in ("chain", "rpc_url", "contract_address", "private_key", "gas_limit_submit", "gas_limit_batch")},
            )
        return cls(config_dir=os.path.dirname(config_dir) or DEFAULT_CONFIG_DIR)

# -----------------------------------------------------------------------------
# Contract ABI (minimal)
# -----------------------------------------------------------------------------

ANGELA_AIX_ABI = [
    {"inputs": [{"name": "clawKind", "type": "uint8"}, {"name": "payloadHash", "type": "bytes32"}, {"name": "minValue", "type": "uint256"}, {"name": "maxValue", "type": "uint256"}], "name": "submitClaw", "outputs": [{"name": "clawId", "type": "uint256"}], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "clawId", "type": "uint256"}, {"name": "actualValue", "type": "uint256"}], "name": "markClawExecuted", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "clawId", "type": "uint256"}, {"name": "reason", "type": "bytes"}], "name": "markClawReverted", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "clawId", "type": "uint256"}], "name": "getClaw", "outputs": [{"name": "clawKind", "type": "uint8"}, {"name": "payloadHash", "type": "bytes32"}, {"name": "minValue", "type": "uint256"}, {"name": "maxValue", "type": "uint256"}, {"name": "operatorAddr", "type": "address"}, {"name": "submittedAtBlock", "type": "uint256"}, {"name": "executed", "type": "bool"}, {"name": "reverted", "type": "bool"}, {"name": "executedAtBlock", "type": "uint256"}, {"name": "actualValue", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "clawCount", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "canSubmitNow", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "operator", "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "guardian", "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "paused", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "emergencyHalt", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "cooldownBlocks", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "rateLimitWindowBlocks", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "rateLimitMaxClaws", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "kind", "type": "uint8"}], "name": "getClawKindName", "outputs": [{"name": "", "type": "string"}], "stateMutability": "pure", "type": "function"},
    {"inputs": [], "name": "contractBalance", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "offset", "type": "uint256"}, {"name": "limit", "type": "uint256"}], "name": "getClawIdsPaginated", "outputs": [{"name": "ids", "type": "uint256[]"}], "stateMutability": "view", "type": "function"},
]

