#!/usr/bin/env python3
"""
MICROCORE (MCX) PHONE MINER v5.0 - FULL VERSION
Real ECDSA secp256k1 | Gossip Discovery | Peer Caching | No DNS Required
Remote Control | Uptime Tracking | Slashing Handling | Level System
Runs on iPhone (a-shell/iSH) and Android (Termux)

Run: python3 phone_miner.py
"""

import json
import time
import hashlib
import os
import sys
import random
import asyncio
import secrets
from datetime import datetime
from typing import Optional, List, Dict, Any

# ==================== DEPENDENCY CHECK ====================
try:
    import websockets
except ImportError:
    print("[SETUP] Installing websockets...")
    os.system("pip install websockets")
    import websockets

try:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature
except ImportError:
    print("[SETUP] Installing cryptography...")
    os.system("pip install cryptography")
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature

# ==================== GOSSIP DISCOVERY (NO DNS) ====================
# HARDCODED BOOTNODES - Only ONE IP needed, miners discover others via gossip
BOOTSTRAP_NODES = [
    "YOUR_SERVER_IP:8080",  # ← CHANGE THIS TO YOUR NODE IP
]

PEER_CACHE_FILE = "phone_miner_peers.json"
NODE_PORT = 8080

def save_peers_to_cache(peers: List[str]) -> None:
    """Save discovered peers to cache file"""
    try:
        unique = list(set(peers))
        with open(PEER_CACHE_FILE, 'w') as f:
            json.dump(unique, f, indent=2)
        print(f"[CACHE] Saved {len(unique)} peers")
    except Exception as e:
        print(f"[CACHE] Save failed: {e}")

def load_peers_from_cache() -> List[str]:
    """Load previously discovered peers from cache"""
    try:
        with open(PEER_CACHE_FILE, 'r') as f:
            peers = json.load(f)
        print(f"[CACHE] Loaded {len(peers)} peers from cache")
        return peers
    except:
        print(f"[CACHE] No cache file found")
        return []

def get_bootstrap_peers() -> List[str]:
    """Combine hardcoded bootnodes with cached peers"""
    peers = BOOTSTRAP_NODES.copy()
    cached = load_peers_from_cache()
    for p in cached:
        if p not in peers:
            peers.append(p)
    return peers

# ==================== CONFIGURATION ====================
USERNAME = ""  # Leave empty for first-run setup
WALLET_FILE = "microcore_phone_wallet.json"

INITIAL_STAKE = 100
LEVEL_STAKE_RANGE = 100
SIGNING_WINDOW_MS = 2500
SLASH_RATE = 0.10
UPTIME_PING_INTERVAL = 30
STATUS_INTERVAL = 60
MAX_RECONNECT_ATTEMPTS = 10
RECONNECT_DELAY = 5
VERSION = "5.0"

# ==================== REAL CRYPTO FUNCTIONS ====================
def generate_private_key() -> tuple:
    """Generate ECDSA secp256k1 private key"""
    private_key = ec.generate_private_key(ec.SECP256K1())
    private_key_hex = private_key.private_numbers().private_value.to_bytes(32, 'big').hex()
    return private_key_hex, private_key

def get_public_key_pem(private_key_hex: str) -> str:
    """Get public key in PEM format from private key hex"""
    private_value = int(private_key_hex, 16)
    private_key = ec.derive_private_key(private_value, ec.SECP256K1())
    public_key = private_key.public_key()
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()

def get_wallet_address(public_key_pem: str) -> str:
    """Generate wallet address from public key"""
    addr_hash = hashlib.sha256(public_key_pem.encode()).hexdigest()
    return f"MCR_{addr_hash[:32].upper()}"

def get_validator_id(username: str, public_key_pem: str) -> str:
    """Generate validator ID from username and public key"""
    return hashlib.sha256(f"{username}{public_key_pem}".encode()).hexdigest()[:32]

def sign_message(private_key_hex: str, message: str) -> str:
    """Sign message with ECDSA secp256k1"""
    private_value = int(private_key_hex, 16)
    private_key = ec.derive_private_key(private_value, ec.SECP256K1())
    signature = private_key.sign(message.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(signature)
    return r.to_bytes(32, 'big').hex() + s.to_bytes(32, 'big').hex()

# ==================== WALLET MANAGEMENT ====================
class Wallet:
    def __init__(self, username: str, address: str, public_key_pem: str, private_key_hex: str):
        self.username = username
        self.address = address
        self.public_key_pem = public_key_pem
        self.private_key_hex = private_key_hex
    
    def get_validator_id(self) -> str:
        return get_validator_id(self.username, self.public_key_pem)
    
    @classmethod
    def create_new(cls, username: str) -> 'Wallet':
        private_key_hex, _ = generate_private_key()
        public_key_pem = get_public_key_pem(private_key_hex)
        address = get_wallet_address(public_key_pem)
        return cls(username, address, public_key_pem, private_key_hex)
    
    @classmethod
    def load(cls, filename: str) -> Optional['Wallet']:
        if not os.path.exists(filename):
            return None
        with open(filename, 'r') as f:
            data = json.load(f)
        return cls(
            username=data.get('username', ''),
            address=data['address'],
            public_key_pem=data['public_key_pem'],
            private_key_hex=data['private_key_hex']
        )
    
    def save(self, filename: str):
        with open(filename, 'w') as f:
            json.dump({
                'username': self.username,
                'address': self.address,
                'public_key_pem': self.public_key_pem,
                'private_key_hex': self.private_key_hex,
                'created_at': time.time(),
                'version': VERSION
            }, f, indent=2)
            
    # ==================== P2P PROTOCOL ====================
P2P_MAGIC = b"MCR1"
P2P_VERSION = 1
MSG_HANDSHAKE, MSG_PING, MSG_PONG, MSG_GET_BLOCKS, MSG_BLOCKS, MSG_NEW_BLOCK, MSG_NEW_TX, MSG_GET_PEERS, MSG_PEERS, MSG_SLASH = range(10)

def encode_p2p(t, p):
    j = json.dumps(p).encode()
    return P2P_MAGIC + struct.pack(">BBI", P2P_VERSION, t, len(j)) + j

def decode_p2p(d):
    if len(d) < 10 or d[:4] != P2P_MAGIC:
        return None, None
    return d[5], json.loads(d[10:10+struct.unpack(">I", d[6:10])[0]].decode())

# ==================== DEX ====================
class DEX:
    def __init__(self, net):
        self.net = net
        self.pools = {pid: {"a": 100000, "b": 100000, "lp": {}, "total_lp": 0} for pid in OWN_POOLS}
        self.mcx_price_usd = MCX_PRICE_USD
    
    def _calc_fee_mcx(self, amount_usd):
        fee_usd = amount_usd * SWAP_FEE_RATE
        fee_mcx = int(fee_usd / self.mcx_price_usd) if self.mcx_price_usd > 0 else MCX_FEE_MIN
        return max(MCX_FEE_MIN, min(fee_mcx, MCX_FEE_MAX))
    
    def quote(self, f, t, amt):
        if f == "MCX" or t == "MCX":
            # Own pool
            pid = f"MCX/{t}" if f == "MCX" else f"MCX/{f}"
            if pid not in self.pools:
                return {"error": "Pool not found"}
            p = self.pools[pid]
            if f == "MCX":
                out = amt * (1 - SWAP_FEE_RATE) * p["b"] / (p["a"] + amt)
            else:
                out = amt * (1 - SWAP_FEE_RATE) * p["a"] / (p["b"] + amt)
            fee = self._calc_fee_mcx(amt * self.mcx_price_usd)
            return {"out": out, "fee": fee, "type": "own"}
        else:
            # LI.FI aggregator mock
            prices = {"BTC": 60000, "ETH": 3000, "SOL": 150, "USDC": 1, "BNB": 300}
            from_price = prices.get(f, 1)
            to_price = prices.get(t, 1)
            value_usd = amt * from_price
            out = (value_usd / to_price) * 0.997
            fee = self._calc_fee_mcx(value_usd)
            return {"out": out, "fee": fee, "type": "lifi"}
    
    def swap(self, wallet, f, t, amt, fee):
        q = self.quote(f, t, amt)
        if q.get("error"):
            return False, q["error"]
        if self.net.balances.get(wallet, 0) < fee:
            return False, "Insufficient MCX for fee"
        
        self.net.balances[wallet] -= fee
        self.net.node_pool += int(fee * 0.4)
        self.net.lp_pool += int(fee * 0.6)
        
        if q["type"] == "own":
            pid = f"MCX/{t}" if f == "MCX" else f"MCX/{f}"
            p = self.pools[pid]
            if f == "MCX":
                p["a"] += amt
                p["b"] -= q["out"]
            else:
                p["b"] += amt
                p["a"] -= q["out"]
        
        tx_hash = hashlib.sha256(f"{wallet}{f}{t}{amt}{time.time()}".encode()).hexdigest()[:16]
        return True, {"tx_hash": tx_hash, "out": q["out"], "fee": fee}
    
    def add_liquidity(self, wallet, pid, amt_a, amt_b):
        if pid not in self.pools:
            return False, "Pool not found"
        if self.net.balances.get(wallet, 0) < amt_a + amt_b:
            return False, "Insufficient balance"
        
        self.net.balances[wallet] -= (amt_a + amt_b)
        p = self.pools[pid]
        p["a"] += amt_a
        p["b"] += amt_b
        lp_shares = (amt_a * amt_b) ** 0.5
        p["total_lp"] += lp_shares
        p["lp"][wallet] = p["lp"].get(wallet, 0) + lp_shares
        return True, {"lp_shares": lp_shares}
    
    def remove_liquidity(self, wallet, pid, lp_shares):
        if pid not in self.pools:
            return False, "Pool not found"
        p = self.pools[pid]
        if wallet not in p["lp"] or p["lp"][wallet] < lp_shares:
            return False, "Insufficient LP shares"
        
        ratio = lp_shares / p["total_lp"] if p["total_lp"] > 0 else 0
        amt_a = p["a"] * ratio
        amt_b = p["b"] * ratio
        
        p["a"] -= amt_a
        p["b"] -= amt_b
        p["total_lp"] -= lp_shares
        p["lp"][wallet] -= lp_shares
        if p["lp"][wallet] <= 0:
            del p["lp"][wallet]
        
        self.net.balances[wallet] = self.net.balances.get(wallet, 0) + amt_a + amt_b
        return True, {"amt_a": amt_a, "amt_b": amt_b}
    
    def buy_mcx(self, wallet, usd, method="card"):
        if not FIAT_RAMP_ENABLED:
            return False, "Fiat ramp disabled"
        mcx = int(usd / self.mcx_price_usd)
        self.net.balances[wallet] = self.net.balances.get(wallet, 0) + mcx
        self.net.total_minted += mcx
        
        # Track buyer for rewards
        c = self.net.conn.cursor()
        c.execute("INSERT OR REPLACE INTO buyer_stats (wallet, username, bought, last_reset) VALUES (?, ?, COALESCE((SELECT bought FROM buyer_stats WHERE wallet=?), 0) + ?, ?)",
                 (wallet, wallet, wallet, mcx, time.time()))
        self.net.conn.commit()
        return True, {"mcx": mcx, "usd": usd}
    
    def get_pools(self):
        pools = []
        for pid, p in self.pools.items():
            pools.append({"id": pid, "a": p["a"], "b": p["b"], "type": "own"})
        pools.append({"type": "aggregator", "name": "LI.FI"})
        return pools

# ==================== LEVEL MANAGER ====================
class LevelManager:
    def __init__(self, net):
        self.net = net
        self.max_unlocked = 1
        self.towers = {}
        self.level_wallets = {}
    
    def register(self, wallet, stake):
        alloc = {}
        rem = stake
        lvl = 1
        while rem > 0:
            if lvl > self.max_unlocked:
                alloc[self.max_unlocked] = alloc.get(self.max_unlocked, 0) + rem
                break
            add = min(rem, LEVEL_STAKE_RANGE)
            alloc[lvl] = alloc.get(lvl, 0) + add
            rem -= add
            lvl += 1
        self.towers[wallet] = alloc
        self._update()
    
    def _update(self):
        self.level_wallets.clear()
        for m in self.net.miners.values():
            lvl = self.get_level(m.wallet)
            if lvl not in self.level_wallets:
                self.level_wallets[lvl] = set()
            self.level_wallets[lvl].add(m.wallet)
        for lvl in list(self.level_wallets.keys()):
            self.level_wallets[lvl] = len(self.level_wallets[lvl])
        while self.max_unlocked + 1 in self.level_wallets and self.level_wallets[self.max_unlocked + 1] >= MIN_WALLETS_FOR_NEXT_LEVEL:
            self.max_unlocked += 1
            print(f"[LEVEL] Level {self.max_unlocked} UNLOCKED!")
            for w, t in self.towers.items():
                if self.max_unlocked in t:
                    for m in self.net.miners.values():
                        if m.wallet == w:
                            m.stake += t[self.max_unlocked]
                            m.level = self.get_level(w)
                            break
                    del t[self.max_unlocked]
    
    def get_level(self, wallet):
        if wallet not in self.towers:
            return 1
        for lvl in range(self.max_unlocked, 0, -1):
            if self.towers[wallet].get(lvl, 0) > 0:
                return lvl
        return 1
        
# ==================== P2P NODE (GOSSIP DISCOVERY) ====================
class P2PNode:
    def __init__(self, net):
        self.net = net
        self.peers = {}
        self.banned_peers = {}
        self.ip = get_public_ip()
    
    async def start(self):
        self.server = await asyncio.start_server(self._handle, NODE_HOST, P2P_PORT)
        print(f"[P2P] Server on port {P2P_PORT}")
        if self.ip:
            print(f"[P2P] Public IP: {self.ip}:{P2P_PORT}")
    
    async def _handle(self, r, w):
        addr = f"{w.get_extra_info('peername')[0]}:{w.get_extra_info('peername')[1]}"
        
        if addr in self.banned_peers:
            if time.time() < self.banned_peers[addr]:
                w.close()
                return
            else:
                del self.banned_peers[addr]
        
        try:
            length = await r.read(4)
            if not length:
                w.close()
                return
            msg_len = struct.unpack(">I", length)[0]
            if msg_len > 10_000_000:
                self._ban_peer(addr, "Message too large")
                w.close()
                return
            data = await r.read(msg_len)
            typ, p = decode_p2p(data)
            if typ is not None:
                await self._process_message(typ, p, w, addr)
        except Exception as e:
            print(f"[P2P] Error: {e}")
        finally:
            w.close()
    
    def _ban_peer(self, addr, reason):
        self.banned_peers[addr] = time.time() + BAN_DURATION
        if addr in self.peers:
            del self.peers[addr]
        print(f"[P2P] Banned {addr}: {reason}")
    
    async def _process_message(self, typ, p, w, addr):
        if typ == MSG_HANDSHAKE:
            self.peers[addr] = type('Peer', (), {'height': p.get('height', 0), 'last_seen': time.time()})()
            response = encode_p2p(MSG_HANDSHAKE, {"height": self.net.height, "ip": self.ip})
            w.write(struct.pack(">I", len(response)) + response)
            await w.drain()
            if p.get('height', 0) > self.net.height:
                asyncio.create_task(self._request_blocks(addr, self.net.height, p['height']))
        
        elif typ == MSG_GET_PEERS:
            peers_list = list(self.peers.keys())[:100]
            response = encode_p2p(MSG_PEERS, {"peers": peers_list})
            w.write(struct.pack(">I", len(response)) + response)
            await w.drain()
        
        elif typ == MSG_PEERS:
            new_peers = []
            for peer in p.get("peers", []):
                if peer not in self.peers and peer != f"{self.ip}:{P2P_PORT}":
                    self.peers[peer] = type('Peer', (), {'height': 0, 'last_seen': time.time()})()
                    new_peers.append(peer)
                    asyncio.create_task(self._connect(peer))
            if new_peers:
                all_peers = list(self.peers.keys())
                save_peers_to_cache(all_peers)
            print(f"[P2P] Received {len(p.get('peers', []))} peers, {len(new_peers)} new")
        
        elif typ == MSG_GET_BLOCKS:
            start, end = p.get("start", 0), p.get("end", self.net.height)
            if end - start > 2000:
                end = start + 2000
            blocks = self.net.get_blocks_range(start, end)
            response = encode_p2p(MSG_BLOCKS, {"blocks": blocks})
            w.write(struct.pack(">I", len(response)) + response)
            await w.drain()
        
        elif typ == MSG_BLOCKS:
            await self.net.import_blocks(p.get("blocks", []))
        
        elif typ == MSG_NEW_BLOCK:
            await self.net.receive_block(p.get("block"))
        
        elif typ == MSG_NEW_TX:
            await self.net.receive_transaction(p.get("tx"))
        
        elif typ == MSG_PING:
            response = encode_p2p(MSG_PONG, {"timestamp": time.time()})
            w.write(struct.pack(">I", len(response)) + response)
            await w.drain()
        
        elif typ == MSG_PONG:
            if addr in self.peers:
                self.peers[addr].last_seen = time.time()
    
    async def _request_blocks(self, peer, start, end):
        try:
            h, p = peer.split(":")
            r, w = await asyncio.open_connection(h, int(p))
            msg = encode_p2p(MSG_GET_BLOCKS, {"start": start, "end": end})
            w.write(struct.pack(">I", len(msg)) + msg)
            await w.drain()
            w.close()
        except Exception as e:
            print(f"[P2P] Request failed: {e}")
    
    async def broadcast_block(self, blk):
        msg = encode_p2p(MSG_NEW_BLOCK, {"block": blk})
        for peer in list(self.peers.keys()):
            try:
                h, p = peer.split(":")
                r, w = await asyncio.open_connection(h, int(p))
                w.write(struct.pack(">I", len(msg)) + msg)
                await w.drain()
                w.close()
            except:
                pass
    
    async def discover(self):
        # Connect to bootstrap peers (hardcoded + cached)
        bootstrap = get_bootstrap_peers()
        for peer in bootstrap:
            if peer not in self.peers:
                asyncio.create_task(self._connect(peer))
        
        # Ask existing peers for more peers
        for peer_addr in list(self.peers.keys()):
            try:
                h, p = peer_addr.split(":")
                r, w = await asyncio.open_connection(h, int(p))
                msg = encode_p2p(MSG_GET_PEERS, {})
                w.write(struct.pack(">I", len(msg)) + msg)
                await w.drain()
                w.close()
            except:
                if peer_addr in self.peers:
                    del self.peers[peer_addr]
    
    async def _connect(self, addr):
        if addr in self.peers:
            return
        try:
            h, p = addr.split(":")
            r, w = await asyncio.open_connection(h, int(p))
            msg = encode_p2p(MSG_HANDSHAKE, {"height": self.net.height, "ip": self.ip})
            w.write(struct.pack(">I", len(msg)) + msg)
            await w.drain()
            w.close()
            self.peers[addr] = type('Peer', (), {'height': 0, 'last_seen': time.time()})()
            print(f"[P2P] Connected to peer: {addr}")
            save_peers_to_cache(list(self.peers.keys()))
        except Exception as e:
            print(f"[P2P] Failed to connect to {addr}: {e}")
    
    async def sync_with_peers(self):
        if not self.peers:
            return
        best_peer = None
        best_height = self.net.height
        for addr, peer in self.peers.items():
            if peer.height > best_height:
                best_height = peer.height
                best_peer = addr
        if best_peer and best_height > self.net.height:
            print(f"[P2P] Syncing from {best_peer}: local={self.net.height}, remote={best_height}")
            await self._request_blocks(best_peer, self.net.height, best_height)
    
    async def heartbeat(self):
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            msg = encode_p2p(MSG_PING, {"timestamp": time.time()})
            for peer_addr in list(self.peers.keys()):
                try:
                    h, p = peer_addr.split(":")
                    r, w = await asyncio.open_connection(h, int(p))
                    w.write(struct.pack(">I", len(msg)) + msg)
                    await w.drain()
                    w.close()
                except:
                    if peer_addr in self.peers:
                        del self.peers[peer_addr]

# ==================== DATA STRUCTURES ====================
@dataclass
class Miner:
    vid: str
    pub: str
    username: str
    wallet: str
    stake: int
    level: int
    uptime: int
    today_uptime: int
    last_ping: float
    active: bool
    rewards: int
    blocks: int
    slashes: int
    misses: int
    mtype: str
    liquidity: int = 0
    fees: int = 0

@dataclass
class Node:
    node_id: str
    username: str
    wallet: str
    ip: str
    port: int
    last_seen: float
    height: int
    active: bool
    rewards_earned: int

@dataclass
class Transaction:
    tx_hash: str
    from_wallet: str
    to_wallet: str
    amount: int
    fee: int
    timestamp: float
    block_id: int
    status: str
    tx_type: str

@dataclass
class Block:
    id: int
    ts: float
    prev: str
    validators: List[str]
    level: int
    sigs: Dict
    hash: str
    reward: int
    tx_count: int = 0

# ==================== MICROCORE NETWORK ====================
class MicroCoreNetwork:
    def __init__(self, is_genesis, username, wallet, priv, pub):
        self.miners = {}
        self.nodes = {}
        self.balances = {}
        self.blocks = []
        self.transactions = []
        self.height = 0
        self.last_hash = "0" * 64
        self.pending_challenges = {}
        self.pending_txs = []
        self.node_pool = 0
        self.uptime_pool = 0
        self.lp_pool = 0
        self.buyer_pool = 0
        self.total_minted = 0
        self.is_genesis = is_genesis
        self.username = username
        self.wallet = wallet
        self.priv = priv
        self.pub = pub
        self.node_id = hashlib.sha256(f"{username}{time.time()}{secrets.token_hex(8)}".encode()).hexdigest()[:16]
        self.last_buyer_distribution = time.time()
        
        self.level_mgr = LevelManager(self)
        self.p2p = P2PNode(self)
        self.dex = DEX(self)
        
        self._init_db()
        if is_genesis:
            self._genesis()
        else:
            self._load()
        self._register_self_miner()
        self._register_self_node()
    
    def _init_db(self):
        self.conn = sqlite3.connect('phone_node.db', check_same_thread=False)
        c = self.conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS miners (
            vid TEXT PRIMARY KEY, pub TEXT, username TEXT, wallet TEXT,
            stake INT, level INT, rewards INT, blocks INT, slashes INT,
            uptime INT, today_uptime INT, type TEXT, liquidity INT, fees INT)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS nodes (
            node_id TEXT PRIMARY KEY, username TEXT, wallet TEXT, ip TEXT,
            port INT, last_seen REAL, height INT, active INT, rewards_earned INT)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS blocks (
            id INT PRIMARY KEY, ts REAL, phash TEXT, validators TEXT,
            lvl INT, hash TEXT, reward INT, tx_count INT)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS transactions (
            tx_hash TEXT PRIMARY KEY, from_wallet TEXT, to_wallet TEXT,
            amount INT, fee INT, timestamp REAL, block_id INT, status TEXT, tx_type TEXT)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS balances (wallet TEXT PRIMARY KEY, bal INT)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS buyer_stats (
            wallet TEXT PRIMARY KEY, username TEXT, bought REAL, last_reset REAL)''')
        
        self.conn.commit()
    
    def _save_balance(self, w, b):
        self.conn.execute("INSERT OR REPLACE INTO balances VALUES (?,?)", (w, b))
        self.conn.commit()
    
    def _genesis(self):
        if self.conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0] == 0:
            self.balances[self.wallet] = 100000
            self.total_minted = 100000
            self._save_balance(self.wallet, 100000)
            print(f"[GENESIS] Created 100,000 MCX for {self.wallet}")
            self._add_block(0, "0"*64, ["genesis"], 1, {})
    
    def _load(self):
        for row in self.conn.execute("SELECT wallet, bal FROM balances"):
            self.balances[row[0]] = row[1]
        
        for row in self.conn.execute("SELECT id, ts, phash, validators, lvl, hash, reward, tx_count FROM blocks ORDER BY id"):
            validators = row[3].split(',') if row[3] else []
            block = Block(row[0], row[1], row[2], validators, row[4], {}, row[5], row[6], row[7])
            self.blocks.append(block)
            if block.id >= self.height:
                self.height = block.id + 1
                self.last_hash = block.hash
        
        for row in self.conn.execute("SELECT * FROM miners"):
            self.miners[row[0]] = Miner(
                row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8],
                0, True, row[9], row[10], row[11], 0, row[12], row[13], row[14]
            )
            self.level_mgr.register(row[3], row[4])
        
        for row in self.conn.execute("SELECT node_id, username, wallet, ip, port, last_seen, height, active, rewards_earned FROM nodes"):
            self.nodes[row[0]] = Node(row[0], row[1], row[2], row[3], row[4], row[5], row[6], bool(row[7]), row[8])
        
        print(f"[LOAD] {len(self.blocks)} blocks, {len(self.miners)} miners, {len(self.nodes)} nodes")
    
    def _register_self_miner(self):
        self.miners[self.username] = Miner(
            self.username, self.pub, self.username, self.wallet, 1000, 1,
            0, 0, time.time(), True, 0, 0, 0, 0, "embedded", 0, 0
        )
        self.level_mgr.register(self.wallet, 1000)
        self.conn.execute("INSERT OR REPLACE INTO miners VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (self.username, self.pub, self.username, self.wallet, 1000, 1,
                          0, 0, 0, 0, 0, "embedded", 0, 0))
        self.conn.commit()
        print(f"[EMBEDDED] Miner '{self.username}' active")
    
    def _register_self_node(self):
        node = Node(self.node_id, self.username, self.wallet, self.p2p.ip or "unknown", P2P_PORT,
                   time.time(), self.height, True, 0)
        self.nodes[self.node_id] = node
        self.conn.execute("INSERT OR REPLACE INTO nodes VALUES (?,?,?,?,?,?,?,?,?)",
                         (node.node_id, node.username, node.wallet, node.ip, node.port,
                          node.last_seen, node.height, 1, node.rewards_earned))
        self.conn.commit()
        print(f"[NODE] Node '{self.username}' registered")
    
    def _add_block(self, bid, prev, validators, level, sigs):
        ts = time.time()
        blk = Block(bid, ts, prev, validators, level, sigs, "", 0, 0)
        blk.hash = hash_block({"id": bid, "ts": ts, "prev": prev, "validators": validators, "level": level})
        self.blocks.append(blk)
        self.height = bid + 1
        self.last_hash = blk.hash
        self.conn.execute("INSERT INTO blocks VALUES (?,?,?,?,?,?,?,?)",
                         (bid, ts, prev, ','.join(validators), level, blk.hash, 0, 0))
        self.conn.commit()
        return blk
    
    def get_balance(self, w):
        return self.balances.get(w, 0)
    
    def get_block_interval(self, level):
        return LEVEL_BLOCK_INTERVALS.get(level, 30)
    
    def get_current_reward(self):
        remaining = TOTAL_SUPPLY_CAP - self.total_minted
        if remaining <= 0:
            return 1
        halvings = self.height // HALVING_INTERVAL
        reward = INITIAL_BLOCK_REWARD // (2 ** halvings)
        return max(reward, 1)
    
    def get_blocks_range(self, start, end):
        blocks = []
        for b in self.blocks:
            if start <= b.id <= end:
                blocks.append({"id": b.id, "ts": b.ts, "prev": b.prev,
                              "validators": b.validators, "level": b.level,
                              "hash": b.hash, "reward": b.reward})
        return blocks
        
# ==================== MINER MANAGEMENT ====================
    def register_miner(self, vid, pub, username, wallet, stake, sig, ts, mtype):
        if not verify_signature(pub, f"{vid}{username}{stake}{ts}", sig, mtype):
            print(f"[REG] Signature failed for {username}")
            return False
        
        self.level_mgr.register(wallet, stake)
        lvl = self.level_mgr.get_level(wallet)
        
        if vid in self.miners:
            m = self.miners[vid]
            m.stake = stake
            m.level = lvl
            m.username = username
            m.wallet = wallet
            m.active = True
            m.mtype = mtype
        else:
            self.miners[vid] = Miner(vid, pub, username, wallet, stake, lvl, 0, 0,
                                     time.time(), True, 0, 0, 0, 0, mtype, 0, 0)
        
        self.conn.execute("INSERT OR REPLACE INTO miners VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (vid, pub, username, wallet, stake, lvl, 0, 0, 0, 0, 0, mtype, 0, 0))
        self.conn.commit()
        print(f"[REG] {username} | Type: {mtype} | Level {lvl} | Stake {stake} MCX")
        return True
    
    def update_miner_uptime(self, vid, uptime, today_uptime=None):
        if vid not in self.miners:
            return
        m = self.miners[vid]
        now = time.time()
        if now - m.last_ping > 86400:
            m.today_uptime = 0
        m.uptime = uptime
        if today_uptime:
            m.today_uptime = min(today_uptime, 86400)
        else:
            m.today_uptime = min(m.today_uptime + UPTIME_PING_INTERVAL, 86400)
        m.last_ping = now
        self.conn.execute("UPDATE miners SET uptime=?, today_uptime=?, last_ping=? WHERE vid=?",
                         (m.uptime, m.today_uptime, now, vid))
        self.conn.commit()
    
    def slash_miner(self, vid, reason, block_id=-1):
        if vid not in self.miners:
            return 0
        m = self.miners[vid]
        slash = max(int(m.stake * SLASH_RATE), LEVEL_STAKE_RANGE)
        m.stake -= slash
        if m.stake < LEVEL_STAKE_RANGE:
            m.stake = LEVEL_STAKE_RANGE
        m.slashes += 1
        m.misses += 1
        
        self.level_mgr.register(m.wallet, m.stake)
        m.level = self.level_mgr.get_level(m.wallet)
        
        if m.slashes >= BAN_THRESHOLD:
            m.active = False
            print(f"[BAN] {m.username} banned after {BAN_THRESHOLD} slashes")
        
        self.conn.execute("UPDATE miners SET stake=?, level=?, slashes=?, active=? WHERE vid=?",
                         (m.stake, m.level, m.slashes, m.active, vid))
        self.conn.commit()
        print(f"[SLASH] {m.username} lost {slash} MCX")
        return slash
    
    def distribute_block_reward(self, block, signers):
        if block.reward > 0:
            return
        reward = self.get_current_reward()
        block.reward = reward
        
        validator_total = int(reward * 0.7)
        node_total = int(reward * 0.08)
        uptime_total = int(reward * 0.05)
        lp_total = int(reward * 0.05)
        buyer_total = int(reward * 0.12)
        
        validator_share = validator_total // max(len(signers), 1)
        
        for vid in signers:
            if vid in self.miners:
                m = self.miners[vid]
                m.rewards += validator_share
                m.stake += validator_share
                m.blocks += 1
                m.misses = 0
                self.balances[m.wallet] = self.balances.get(m.wallet, 0) + validator_share
                self._save_balance(m.wallet, self.balances[m.wallet])
                self.level_mgr.register(m.wallet, m.stake)
                m.level = self.level_mgr.get_level(m.wallet)
        
        self.node_pool += node_total
        self.uptime_pool += uptime_total
        self.lp_pool += lp_total
        self.buyer_pool += buyer_total
        self.total_minted += reward
        
        print(f"[BLOCK {block.id}] REWARD: {reward} MCX | Validators: {validator_share} each")
    
    def distribute_periodic_rewards(self):
        active_miners = [m for m in self.miners.values() if m.active]
        total_uptime = sum(m.uptime for m in active_miners)
        if total_uptime > 0 and self.uptime_pool > 0:
            for miner in active_miners:
                if miner.uptime > 0:
                    share = int(self.uptime_pool * (miner.uptime / total_uptime))
                    miner.rewards += share
                    miner.stake += share
                    self.balances[miner.wallet] = self.balances.get(miner.wallet, 0) + share
                    self._save_balance(miner.wallet, self.balances[miner.wallet])
                    self.level_mgr.register(miner.wallet, miner.stake)
                    miner.level = self.level_mgr.get_level(miner.wallet)
                    self.conn.execute("UPDATE miners SET stake=?, level=?, rewards=? WHERE vid=?",
                                     (miner.stake, miner.level, miner.rewards, miner.vid))
            print(f"[DISTRO] Uptime rewards: {self.uptime_pool} MCX to {len(active_miners)} miners")
        
        active_nodes = [n for n in self.nodes.values() if n.active]
        if active_nodes and self.node_pool > 0:
            node_share = self.node_pool // max(len(active_nodes), 1)
            for node in active_nodes:
                node.rewards_earned += node_share
                self.balances[node.wallet] = self.balances.get(node.wallet, 0) + node_share
                self._save_balance(node.wallet, self.balances[node.wallet])
                self.conn.execute("UPDATE nodes SET rewards_earned=? WHERE node_id=?", (node.rewards_earned, node.node_id))
            print(f"[DISTRO] Node rewards: {self.node_pool} MCX to {len(active_nodes)} nodes")
        
        self.node_pool = 0
        self.uptime_pool = 0
        self.lp_pool = 0
    
    def distribute_buyer_rewards(self):
        if self.buyer_pool == 0:
            return
        c = self.conn.cursor()
        c.execute("SELECT wallet, username, bought FROM buyer_stats WHERE last_reset > ? ORDER BY bought DESC LIMIT 10",
                 (time.time() - 30 * 24 * 3600,))
        top_buyers = c.fetchall()
        if not top_buyers:
            return
        for i, (wallet, username, _) in enumerate(top_buyers):
            if i >= len(BUYER_REWARDS):
                break
            reward = min(BUYER_REWARDS[i], self.buyer_pool)
            self.balances[wallet] = self.balances.get(wallet, 0) + reward
            self.buyer_pool -= reward
            print(f"[BUYER REWARD] #{i+1} {username[:20]}... +{reward} MCX")
        c.execute("UPDATE buyer_stats SET bought = 0, last_reset = ?", (time.time(),))
        self.conn.commit()
        self.buyer_pool = 0
        
# ==================== CONSENSUS & BLOCK PRODUCTION ====================
    def update_level_groups(self):
        self.level_groups = defaultdict(list)
        for m in self.miners.values():
            if m.active:
                self.level_groups[m.level].append(m.vid)
    
    def select_validators(self, level):
        miners = self.level_groups.get(level, [])
        if len(miners) < MIN_VALIDATORS_PER_BLOCK:
            return []
        seed = int(self.last_hash[:16], 16) if self.last_hash != "0"*64 else int(time.time())
        rng = random.Random(seed)
        return rng.sample(miners, MIN_VALIDATORS_PER_BLOCK)
    
    def generate_challenge(self, block_id, validators):
        return hashlib.sha256(
            f"{block_id}{''.join(sorted(validators))}{time.time()}{self.last_hash}{secrets.token_hex(16)}".encode()
        ).hexdigest()
    
    def verify_challenge_response(self, vid, challenge, block_id, sig):
        if vid not in self.miners:
            return False
        message = f"{challenge}{vid}{block_id}"
        return verify_signature(self.miners[vid].pub, message, sig, self.miners[vid].mtype)
    
    async def produce_block(self):
        self.update_level_groups()
        for level in sorted(self.level_groups.keys(), reverse=True):
            validators = self.select_validators(level)
            if len(validators) < MIN_VALIDATORS_PER_BLOCK:
                continue
            
            block_id = self.height
            challenge = self.generate_challenge(block_id, validators)
            self.pending_challenges[challenge] = {"bid": block_id, "validators": validators, "level": level, "sigs": {}}
            
            await asyncio.sleep(SIGNING_WINDOW_MS / 1000)
            pending = self.pending_challenges.pop(challenge, {})
            sigs = pending.get("sigs", {})
            valid_sigs = {}
            total_slashed = 0
            
            for vid, sig in sigs.items():
                if self.verify_challenge_response(vid, challenge, block_id, sig):
                    valid_sigs[vid] = sig
            
            if len(valid_sigs) >= MIN_VALIDATORS_PER_BLOCK:
                block = self._add_block(block_id, self.last_hash, list(valid_sigs.keys()), level, valid_sigs)
                self.distribute_block_reward(block, list(valid_sigs.keys()))
                asyncio.create_task(self.p2p.broadcast_block({
                    "id": block_id, "ts": block.ts, "prev": block.prev,
                    "validators": block.validators, "level": level,
                    "hash": block.hash, "reward": block.reward
                }))
                interval = self.get_block_interval(level)
                print(f"[BLOCK {block_id}] ✅ ACCEPTED | Level {level} | Validators: {len(valid_sigs)} | Next block in {interval}s")
                await asyncio.sleep(interval)
            else:
                missing = set(validators) - set(sigs.keys())
                for vid in missing:
                    total_slashed += self.slash_miner(vid, f"Missed signing for block {block_id}", block_id)
                if total_slashed > 0 and len(valid_sigs) > 0:
                    per_signer = total_slashed // len(valid_sigs)
                    for vid in valid_sigs:
                        self.miners[vid].stake += per_signer
                        self.miners[vid].rewards += per_signer
                        self.balances[self.miners[vid].wallet] = self.balances.get(self.miners[vid].wallet, 0) + per_signer
                        self.conn.execute("UPDATE miners SET stake=?, rewards=? WHERE vid=?",
                                         (self.miners[vid].stake, self.miners[vid].rewards, vid))
                        self._save_balance(self.miners[vid].wallet, self.balances[self.miners[vid].wallet])
                    print(f"[REDIST] {total_slashed} MCX redistributed to {len(valid_sigs)} signers")
                print(f"[BLOCK {block_id}] ❌ REJECTED | Got {len(valid_sigs)}/{MIN_VALIDATORS_PER_BLOCK} signatures")
            return
    
    async def import_blocks(self, blocks_data):
        for b in sorted(blocks_data, key=lambda x: x['id']):
            if b['id'] >= self.height and b['prev'] == self.last_hash:
                block = Block(b['id'], b['ts'], b['prev'], b['validators'], b['level'], {}, b['hash'], b.get('reward', 0), 0)
                self.blocks.append(block)
                self.height = b['id'] + 1
                self.last_hash = block.hash
                self.conn.execute("INSERT INTO blocks VALUES (?,?,?,?,?,?,?,?)",
                                 (b['id'], b['ts'], b['prev'], ','.join(b['validators']),
                                  b['level'], b['hash'], b.get('reward', 0), 0))
                self.conn.commit()
                print(f"[SYNC] Imported block {b['id']}")
    
    async def receive_block(self, block_data):
        bid = block_data.get('id')
        if bid == self.height and block_data.get('prev') == self.last_hash:
            block = Block(bid, block_data['ts'], block_data['prev'], block_data['validators'],
                         block_data['level'], {}, block_data['hash'], block_data.get('reward', 0), 0)
            self.blocks.append(block)
            self.height = bid + 1
            self.last_hash = block.hash
            self.conn.execute("INSERT INTO blocks VALUES (?,?,?,?,?,?,?,?)",
                             (bid, block.ts, block.prev, ','.join(block.validators),
                              block.level, block.hash, block.reward, 0))
            self.conn.commit()
            print(f"[P2P] Received block {bid}")
    
    async def receive_transaction(self, tx_data):
        print(f"[P2P] Received transaction from peer")
        
# ==================== WEBSOCKET SERVER ====================
    async def ws_handler(self, websocket, path):
        try:
            async for message in websocket:
                data = json.loads(message)
                t = data.get("type")
                
                if t == "register":
                    ok = self.register_miner(
                        data["validator_id"], data["public_key"], data["username"],
                        data["wallet"], data["stake"], data["signature"],
                        data["timestamp"], data.get("miner_type", "web")
                    )
                    if ok:
                        await websocket.send(json.dumps({
                            "type": "registered",
                            "level": self.level_mgr.get_level(data["wallet"]),
                            "current_reward": self.get_current_reward(),
                            "dex_pools": OWN_POOLS
                        }))
                
                elif t == "block_signature":
                    ch = data["challenge"]
                    if ch in self.pending_challenges:
                        self.pending_challenges[ch]["sigs"][data["validator_id"]] = data["signature"]
                
                elif t == "uptime_ping":
                    self.update_miner_uptime(data["validator_id"], data.get("uptime_seconds", 0), data.get("today_uptime", 0))
                
                elif t == "get_peers":
                    peers = [f"{addr}" for addr in self.p2p.peers.keys()]
                    await websocket.send(json.dumps({"type": "peers", "peers": peers}))
                
                elif t == "stake":
                    amt = data["amount"]
                    if self.get_balance(self.wallet) >= amt:
                        self.balances[self.wallet] -= amt
                        self.miners[self.username].stake += amt
                        self.level_mgr.register(self.wallet, self.miners[self.username].stake)
                        await websocket.send(json.dumps({"type": "staked", "staked": self.miners[self.username].stake}))
                
                elif t == "unstake":
                    amt = data["amount"]
                    if self.miners[self.username].stake >= amt:
                        self.miners[self.username].stake -= amt
                        self.balances[self.wallet] = self.balances.get(self.wallet, 0) + amt
                        self.level_mgr.register(self.wallet, self.miners[self.username].stake)
                        await websocket.send(json.dumps({"type": "unstaked", "staked": self.miners[self.username].stake}))
                
                elif t == "send":
                    to = data["to"]
                    amt = data["amount"]
                    to_wallet = None
                    for m in self.miners.values():
                        if m.username == to:
                            to_wallet = m.wallet
                            break
                    if to_wallet and self.get_balance(self.wallet) >= amt:
                        self.balances[self.wallet] -= amt
                        self.balances[to_wallet] = self.balances.get(to_wallet, 0) + amt
                        self._save_balance(self.wallet, self.balances[self.wallet])
                        self._save_balance(to_wallet, self.balances[to_wallet])
                        await websocket.send(json.dumps({"type": "sent", "to": to, "amount": amt}))
                
                elif t == "swap_quote":
                    q = self.dex.quote(data["from"], data["to"], data["amount"])
                    await websocket.send(json.dumps({"type": "swap_quote", "data": q}))
                
                elif t == "execute_swap":
                    ok, result = self.dex.swap(data["wallet"], data["from"], data["to"], data["amount"], data.get("fee_mcx", 5))
                    await websocket.send(json.dumps({"type": "swap_result", "success": ok, "data": result}))
                
                elif t == "add_liquidity":
                    ok, result = self.dex.add_liquidity(data["wallet"], data["pool"], data["amount_a"], data["amount_b"])
                    await websocket.send(json.dumps({"type": "liquidity_result", "success": ok, "data": result}))
                
                elif t == "remove_liquidity":
                    ok, result = self.dex.remove_liquidity(data["wallet"], data["pool"], data["lp_shares"])
                    await websocket.send(json.dumps({"type": "liquidity_result", "success": ok, "data": result}))
                
                elif t == "buy_mcx":
                    ok, result = self.dex.buy_mcx(data["wallet"], data["usd"], data.get("method", "card"))
                    await websocket.send(json.dumps({"type": "buy_result", "success": ok, "data": result}))
                
                elif t == "get_balance":
                    await websocket.send(json.dumps({"type": "balance", "balance": self.get_balance(data["wallet"])}))
                
                elif t == "get_miners":
                    miners = [{"vid": m.vid, "username": m.username, "level": m.level, "stake": m.stake, "blocks": m.blocks, "today_uptime": m.today_uptime} for m in self.miners.values()]
                    await websocket.send(json.dumps({"type": "miners", "miners": miners}))
                
                elif t == "get_nodes":
                    nodes = [{"node_id": n.node_id, "username": n.username, "ip": n.ip, "height": n.height, "rewards": n.rewards_earned} for n in self.nodes.values()]
                    await websocket.send(json.dumps({"type": "nodes", "nodes": nodes}))
                
                elif t == "get_top_stakers":
                    stakers = sorted([{"username": m.username, "staked": m.stake} for m in self.miners.values()], key=lambda x: x["staked"], reverse=True)[:10]
                    await websocket.send(json.dumps({"type": "top_stakers", "stakers": stakers}))
                
                elif t == "get_top_buyers":
                    c = self.conn.cursor()
                    c.execute("SELECT username, bought FROM buyer_stats ORDER BY bought DESC LIMIT 10")
                    buyers = [{"username": r[0], "bought": r[1]} for r in c.fetchall()]
                    await websocket.send(json.dumps({"type": "top_buyers", "buyers": buyers}))
                
                elif t == "get_blocks":
                    limit = data.get("limit", 20)
                    blocks = []
                    for b in self.blocks[-limit:]:
                        blocks.append({"id": b.id, "ts": b.ts, "hash": b.hash, "validators": b.validators, "reward": b.reward})
                    await websocket.send(json.dumps({"type": "blocks", "blocks": blocks, "total": len(self.blocks)}))
                
                elif t == "get_status":
                    await websocket.send(json.dumps({"type": "status", "data": {
                        "block_id": self.height,
                        "total_miners": len(self.miners),
                        "total_nodes": len(self.nodes),
                        "current_reward": self.get_current_reward(),
                        "total_minted": self.total_minted,
                        "remaining_supply": TOTAL_SUPPLY_CAP - self.total_minted,
                        "max_level": self.level_mgr.max_unlocked
                    }}))
        
        except Exception as e:
            print(f"[WS] Error: {e}")

# ==================== MAIN ====================
class PhoneNodeServer:
    def __init__(self, network):
        self.network = network
    
    async def block_production_loop(self):
        while True:
            await self.network.produce_block()
            await asyncio.sleep(2)
    
    async def peer_discovery_loop(self):
        while True:
            await asyncio.sleep(PEX_INTERVAL)
            await self.network.p2p.discover()
    
    async def peer_sync_loop(self):
        while True:
            await asyncio.sleep(SYNC_INTERVAL)
            await self.network.p2p.sync_with_peers()
    
    async def periodic_distribution_loop(self):
        while True:
            await asyncio.sleep(DISTRIBUTION_INTERVAL_SEC)
            self.network.distribute_periodic_rewards()
    
    async def buyer_rewards_loop(self):
        while True:
            await asyncio.sleep(3600)
            if time.time() - self.network.last_buyer_distribution > 30 * 24 * 3600:
                self.network.distribute_buyer_rewards()
                self.network.last_buyer_distribution = time.time()
    
    async def embedded_miner_loop(self):
        while True:
            for challenge, pending in self.network.pending_challenges.items():
                vid = self.network.username
                if vid in pending["validators"] and vid not in pending["sigs"]:
                    sig = sign_message(self.network.priv, f"{challenge}{vid}{pending['bid']}")
                    pending["sigs"][vid] = sig
                    print(f"[EMBEDDED] Signed block {pending['bid']}")
            await asyncio.sleep(0.2)
    
    async def status_reporter_loop(self):
        while True:
            await asyncio.sleep(60)
            remaining = TOTAL_SUPPLY_CAP - self.network.total_minted
            percent = (self.network.total_minted / TOTAL_SUPPLY_CAP) * 100 if TOTAL_SUPPLY_CAP > 0 else 0
            print(f"\n[STATUS] Height: {self.network.height} | Reward: {self.network.get_current_reward()} MCX")
            print(f"[STATUS] Miners: {len(self.network.miners)} | Nodes: {len(self.network.nodes)} | Peers: {len(self.network.p2p.peers)}")
            print(f"[SUPPLY] {self.network.total_minted:,} / {TOTAL_SUPPLY_CAP:,} ({percent:.2f}%)\n")
    
    async def run(self):
        asyncio.create_task(self.network.p2p.start())
        asyncio.create_task(self.network.p2p.heartbeat())
        asyncio.create_task(self.peer_discovery_loop())
        asyncio.create_task(self.peer_sync_loop())
        asyncio.create_task(self.periodic_distribution_loop())
        asyncio.create_task(self.buyer_rewards_loop())
        asyncio.create_task(self.block_production_loop())
        asyncio.create_task(self.embedded_miner_loop())
        asyncio.create_task(self.status_reporter_loop())
        
        async with serve(self.network.ws_handler, NODE_HOST, NODE_PORT):
            print(f"\n{'='*60}")
            print(f"📱 MICROCORE PHONE NODE v{VERSION}")
            print(f"{'='*60}")
            print(f"Username: {self.network.username}")
            print(f"Wallet: {self.network.wallet}")
            print(f"WebSocket: ws://0.0.0.0:{NODE_PORT}")
            print(f"P2P: 0.0.0.0:{P2P_PORT}")
            print(f"Bootnodes: {BOOTSTRAP_NODES}")
            print(f"GOSSIP DISCOVERY: ON")
            print(f"EMBEDDED MINER: ACTIVE")
            print(f"{'='*60}\n")
            await asyncio.Future()

async def main():
    parser = argparse.ArgumentParser(description=f'{NAME} Phone Node')
    parser.add_argument('--genesis', action='store_true', help='Run as genesis node')
    parser.add_argument('--peer', type=str, help='Connect to peer node (IP:PORT)')
    parser.add_argument('--username', type=str, required=True, help='Your username')
    parser.add_argument('--wallet', type=str, default="", help='Your wallet address')
    parser.add_argument('--privkey', type=str, default="", help='Your private key')
    args = parser.parse_args()
    
    print("\n" + "=" * 60)
    print(f"📱 {NAME} PHONE NODE v{VERSION}")
    print("=" * 60)
    
    if args.wallet and args.privkey:
        my_wallet = args.wallet
        my_priv = args.privkey
        priv_obj = ec.derive_private_key(int(my_priv, 16), ec.SECP256K1())
        pub = priv_obj.public_key()
        my_pub = pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        print(f"[WALLET] Using existing wallet: {my_wallet}")
    elif args.wallet:
        my_wallet = args.wallet
        _, my_priv, my_pub = generate_wallet()
        print(f"[WALLET] Using wallet: {my_wallet} (new private key generated)")
    else:
        my_wallet, my_priv, my_pub = generate_wallet()
        print(f"\n🆕 NEW WALLET CREATED!")
        print(f"Wallet: {my_wallet}")
        print(f"Private Key: {my_priv}")
        print(f"SAVE THESE!\n")
    
    network = MicroCoreNetwork(args.genesis, args.username, my_wallet, my_priv, my_pub)
    server = PhoneNodeServer(network)
    
    if args.peer:
        await network.p2p._connect(args.peer)
    
    await server.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Phone node stopped")
        sys.exit(0)
        
