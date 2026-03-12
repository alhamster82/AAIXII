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

