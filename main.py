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

# -----------------------------------------------------------------------------
# Client
# -----------------------------------------------------------------------------

def to_hex(b: bytes) -> str:
    return "0x" + b.hex()

def to_bytes32(s: str) -> bytes:
    s = s.replace("0x", "").zfill(64)
    return bytes.fromhex(s)[:32]

class AngelaAIXClient:
    def __init__(self, rpc_url: str, contract_address: str, private_key: Optional[str] = None):
        if not WEB3_AVAILABLE:
            raise RuntimeError("web3 and eth_account required. pip install web3 eth-account")
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.contract_address = Web3.to_checksum_address(contract_address)
        self.contract = self.w3.eth.contract(address=self.contract_address, abi=ANGELA_AIX_ABI)
        self.private_key = private_key
        self.account = Account.from_key(private_key) if private_key else None

    def is_connected(self) -> bool:
        return self.w3.is_connected()

    def claw_count(self) -> int:
        return self.contract.functions.clawCount().call()

    def can_submit_now(self) -> bool:
        return self.contract.functions.canSubmitNow().call()

    def get_claw(self, claw_id: int) -> tuple:
        return self.contract.functions.getClaw(claw_id).call()

    def get_operator(self) -> str:
        return self.contract.functions.operator().call()

    def get_guardian(self) -> str:
        return self.contract.functions.guardian().call()

    def is_paused(self) -> bool:
        return self.contract.functions.paused().call()

    def is_halted(self) -> bool:
        return self.contract.functions.emergencyHalt().call()

    def get_cooldown_blocks(self) -> int:
        return self.contract.functions.cooldownBlocks().call()

    def get_rate_limit_window(self) -> int:
        return self.contract.functions.rateLimitWindowBlocks().call()

    def get_rate_limit_max(self) -> int:
        return self.contract.functions.rateLimitMaxClaws().call()

    def get_balance(self) -> int:
        return self.contract.functions.contractBalance().call()

    def get_claw_ids_paginated(self, offset: int, limit: int) -> list:
        return self.contract.functions.getClawIdsPaginated(offset, limit).call()

    def submit_claw(self, claw_kind: int, payload_hash: str, min_value: int, max_value: int) -> str:
        if not self.account:
            raise ValueError("Private key required")
        payload_b32 = to_bytes32(payload_hash)
        tx = self.contract.functions.submitClaw(
            claw_kind,
            payload_b32,
            min_value,
            max_value
        ).build_transaction({"from": self.account.address, "gas": 250_000})
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return tx_hash.hex()

    def mark_executed(self, claw_id: int, actual_value: int) -> str:
        if not self.account:
            raise ValueError("Private key required")
        tx = self.contract.functions.markClawExecuted(claw_id, actual_value).build_transaction(
            {"from": self.account.address, "gas": 150_000}
        )
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return tx_hash.hex()

    def mark_reverted(self, claw_id: int, reason: bytes) -> str:
        if not self.account:
            raise ValueError("Private key required")
        tx = self.contract.functions.markClawReverted(claw_id, reason).build_transaction(
            {"from": self.account.address, "gas": 150_000}
        )
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return tx_hash.hex()

# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def cmd_info(config: AAIXIIConfig) -> None:
    if not config.contract_address or not WEB3_AVAILABLE:
        LOG.warning("Set contract_address and install web3")
        return
    client = AngelaAIXClient(config.effective_rpc, config.contract_address)
    if not client.is_connected():
        LOG.error("RPC not connected")
        return
    count = client.claw_count()
    can_submit = client.can_submit_now()
    paused = client.is_paused()
    halted = client.is_halted()
    balance = client.get_balance()
    op = client.get_operator()
    guard = client.get_guardian()
    LOG.info("Claw count: %s | Can submit: %s | Paused: %s | Halted: %s", count, can_submit, paused, halted)
    LOG.info("Balance: %s wei | Operator: %s | Guardian: %s", balance, op[:16], guard[:16])

def cmd_list_claws(config: AAIXIIConfig, limit: int = 20, offset: int = 0) -> None:
    if not config.contract_address or not WEB3_AVAILABLE:
        LOG.warning("Set contract_address and install web3")
        return
    client = AngelaAIXClient(config.effective_rpc, config.contract_address)
    ids = client.get_claw_ids_paginated(offset, limit)
    for i in ids:
        try:
            kind, payload, min_v, max_v, op, sub_block, executed, reverted, exec_block, actual = client.get_claw(i)
            name = CLAW_KIND_NAMES[kind] if kind < len(CLAW_KIND_NAMES) else "?"
            LOG.info("  [%s] kind=%s min=%s max=%s executed=%s reverted=%s", i, name, min_v, max_v, executed, reverted)
        except Exception as e:
            LOG.debug("claw %s: %s", i, e)

def cmd_submit(config: AAIXIIConfig, kind: int, payload_hash: str, min_val: int, max_val: int) -> None:
    if not config.private_key or not config.contract_address:
        LOG.error("Set private_key and contract_address")
        return
    if not WEB3_AVAILABLE:
        LOG.error("web3 required")
        return
    client = AngelaAIXClient(config.effective_rpc, config.contract_address, config.private_key)
    tx_hash = client.submit_claw(kind, payload_hash, min_val, max_val)
    LOG.info("Submit tx: %s", tx_hash)

def cmd_mark_executed(config: AAIXIIConfig, claw_id: int, actual_value: int) -> None:
    if not config.private_key or not config.contract_address:
        LOG.error("Set private_key and contract_address")
        return
    if not WEB3_AVAILABLE:
        LOG.error("web3 required")
        return
    client = AngelaAIXClient(config.effective_rpc, config.contract_address, config.private_key)
    tx_hash = client.mark_executed(claw_id, actual_value)
    LOG.info("Mark executed tx: %s", tx_hash)

def cmd_mark_reverted(config: AAIXIIConfig, claw_id: int, reason: str = "") -> None:
    if not config.private_key or not config.contract_address:
        LOG.error("Set private_key and contract_address")
        return
    if not WEB3_AVAILABLE:
        LOG.error("web3 required")
        return
    client = AngelaAIXClient(config.effective_rpc, config.contract_address, config.private_key)
