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
    tx_hash = client.mark_reverted(claw_id, reason.encode() if reason else b"")
    LOG.info("Mark reverted tx: %s", tx_hash)

def cmd_init(config: AAIXIIConfig, chain: str, contract: Optional[str], rpc: Optional[str]) -> None:
    config.chain = chain
    if contract:
        config.contract_address = contract
    if rpc:
        config.rpc_url = rpc
    config.save()
    LOG.info("Config saved to %s", config.config_path(DEFAULT_CONFIG_FILE))

# -----------------------------------------------------------------------------
# Validation and helpers
# -----------------------------------------------------------------------------

def validate_address(addr: str) -> bool:
    if not addr or len(addr) < 40:
        return False
    addr = addr.replace("0x", "")
    return len(addr) == 40 and all(c in "0123456789abcdefABCDEF" for c in addr)

def validate_bytes32(s: str) -> bool:
    s = s.replace("0x", "")
    return len(s) == 64 and all(c in "0123456789abcdefABCDEF" for c in s)

def validate_claw_kind(kind: int) -> bool:
    return 1 <= kind <= MAX_CLAW_KIND

def claw_kind_to_name(kind: int) -> str:
    return CLAW_KIND_NAMES[kind] if 1 <= kind < len(CLAW_KIND_NAMES) else "unknown"

def claw_name_to_kind(name: str) -> int:
    name = name.lower().strip()
    for i, n in enumerate(CLAW_KIND_NAMES):
        if n and n == name:
            return i
    return 0

def wei_to_ether(wei: int) -> float:
    return wei / 1e18

def ether_to_wei(ether: float) -> int:
    return int(ether * 1e18)

def format_wei(wei: int) -> str:
    if wei >= 1e18:
        return f"{wei_to_ether(wei):.4f} ETH"
    return f"{wei} wei"

def list_claw_kinds() -> list[tuple[int, str]]:
    return [(i, CLAW_KIND_NAMES[i]) for i in range(1, len(CLAW_KIND_NAMES)) if CLAW_KIND_NAMES[i]]

def get_version() -> str:
    return f"{APP_NAME} {APP_VERSION}"

def config_from_env() -> dict[str, Any]:
    out = {}
    if os.environ.get("AAIXII_RPC_URL"):
        out["rpc_url"] = os.environ.get("AAIXII_RPC_URL")
    if os.environ.get("AAIXII_CONTRACT"):
        out["contract_address"] = os.environ.get("AAIXII_CONTRACT")
    if os.environ.get("AAIXII_CHAIN"):
        out["chain"] = os.environ.get("AAIXII_CHAIN")
    if os.environ.get("AAIXII_PRIVATE_KEY"):
        out["private_key"] = os.environ.get("AAIXII_PRIVATE_KEY")
    return out

def apply_env_to_config(config: AAIXIIConfig) -> AAIXIIConfig:
    for k, v in config_from_env().items():
        setattr(config, k, v)
    return config

def health_check(config: AAIXIIConfig) -> dict[str, Any]:
    result = {"ok": False, "rpc": config.effective_rpc, "contract": config.contract_address, "web3": WEB3_AVAILABLE}
    if not WEB3_AVAILABLE or not config.contract_address:
        return result
    try:
        client = AngelaAIXClient(config.effective_rpc, config.contract_address)
        result["connected"] = client.is_connected()
        result["paused"] = client.is_paused() if result["connected"] else None
        result["clawCount"] = client.claw_count() if result["connected"] else None
        result["ok"] = result["connected"]
    except Exception as e:
        result["error"] = str(e)
    return result

def cmd_health(config: AAIXIIConfig) -> None:
    h = health_check(config)
    for k, v in h.items():
        LOG.info("  %s: %s", k, v)

def cmd_version(config: AAIXIIConfig) -> None:
    print(get_version())

def cmd_kinds(config: AAIXIIConfig) -> None:
    for kind, name in list_claw_kinds():
        LOG.info("  %s: %s", kind, name)

def random_payload_hash() -> str:
    import secrets
    return "0x" + secrets.token_hex(32)

def build_payload_hash_from_string(s: str) -> str:
    import hashlib
    h = hashlib.sha256(s.encode()).digest()
    return "0x" + h.hex()

# -----------------------------------------------------------------------------
# Batch submit (multiple claws in one tx if contract supports it)
# -----------------------------------------------------------------------------

ANGELA_AIX_ABI_BATCH = [
    {"inputs": [{"name": "clawKinds", "type": "uint8[]"}, {"name": "payloadHashes", "type": "bytes32[]"}, {"name": "minValues", "type": "uint256[]"}, {"name": "maxValues", "type": "uint256[]"}], "name": "submitClawBatch", "outputs": [{"name": "clawIds", "type": "uint256[]"}], "stateMutability": "nonpayable", "type": "function"},
]

def submit_claw_batch(client: AngelaAIXClient, kinds: list[int], payload_hashes: list[str], min_vals: list[int], max_vals: list[int]) -> str:
    if not client.account:
        raise ValueError("Private key required")
    n = len(kinds)
    if n != len(payload_hashes) or n != len(min_vals) or n != len(max_vals):
        raise ValueError("Length mismatch")
    if n > MAX_CLAWS_PER_BATCH:
        raise ValueError(f"Max {MAX_CLAWS_PER_BATCH} per batch")
    payloads_b32 = [to_bytes32(p) for p in payload_hashes]
    abi = ANGELA_AIX_ABI + ANGELA_AIX_ABI_BATCH
    from web3 import Web3
    contract = client.w3.eth.contract(address=client.contract_address, abi=abi)
    tx = contract.functions.submitClawBatch(kinds, payloads_b32, min_vals, max_vals).build_transaction(
        {"from": client.account.address, "gas": 600_000}
    )
    signed = client.w3.eth.account.sign_transaction(tx, client.account.key)
    tx_hash = client.w3.eth.send_raw_transaction(signed.raw_transaction)
    return tx_hash.hex()

def cmd_submit_batch(config: AAIXIIConfig, kinds_str: str, payloads_str: str, mins_str: str, maxs_str: str) -> None:
    if not config.private_key or not config.contract_address or not WEB3_AVAILABLE:
        LOG.error("Set private_key, contract_address, and install web3")
        return
    kinds = [int(x) for x in kinds_str.split(",")]
    payloads = [x.strip() for x in payloads_str.split(",")]
    mins = [int(x) for x in mins_str.split(",")]
    maxs = [int(x) for x in maxs_str.split(",")]
    if len(kinds) != len(payloads) or len(kinds) != len(mins) or len(kinds) != len(maxs):
        LOG.error("Length mismatch in batch args")
        return
    client = AngelaAIXClient(config.effective_rpc, config.contract_address, config.private_key)
    try:
        tx_hash = submit_claw_batch(client, kinds, payloads, mins, maxs)
        LOG.info("Batch submit tx: %s", tx_hash)
    except Exception as e:
        LOG.error("Batch submit failed: %s", e)

# -----------------------------------------------------------------------------
# Register batch and extra commands
# -----------------------------------------------------------------------------

def register_commands(sub: Any, parser: Any) -> None:
    p_health = sub.add_parser("health", help="Health check")
    p_health.set_defaults(func=lambda a, c: cmd_health(c))
