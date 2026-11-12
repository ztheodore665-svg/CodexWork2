import socket
import threading
import time
import json
import sys
import secrets
import random
from typing import Dict, List, Tuple, Set

# ipTable
ipTable: List[Tuple[str, int]] = [
    ("127.0.0.1", 32217 + i * 10) for i in range(20)
]

# 要求网络中节点规模在 4 - 20 之间
MIN_NODES = 4
MAX_NODES = 20

# 合并区块多行日志，避免多个节点线程的输出互相穿插。
_output_lock = threading.Lock()


def log(*args, **kwargs):
    """串行写入日志，避免节点并发输出互相拼接。"""
    kwargs.setdefault("flush", True)
    with _output_lock:
        sys.stdout.write(kwargs.get("sep", " ").join(str(arg) for arg in args)
                         + kwargs.get("end", "\n"))
        sys.stdout.flush()


def print_block_event(header: str, block_id: str, origin: int, prev_hash: str, data: str):
    log(f"{header}\n    block_id: {block_id}\n    origin: {origin}"
        f"\n    prev_hash: {prev_hash}\n    data: {data}")

# ============ 实验可修改区域 ============
# 挖矿节点列表：默认仅 0 号节点挖矿。
# TODO（实验任务1·必做）：把它改成多个挖矿节点，例如 [0, 1] 或 [0, 1, 2]，
#   使网络中存在两个或多个同时出块的矿工。
MINER_IDS = [0, 1]

# Block
class Block:
    def __init__(self, block_id: str, prev_hash: str = "0", data: str = "", origin: int = 0):
        self.block_id = block_id
        self.prev_hash = prev_hash
        self.data = data
        self.origin = origin

    def to_dict(self):
        return {
            "block_id": self.block_id,
            "prev_hash": self.prev_hash,
            "data": self.data,
            "origin": self.origin
        }

    @classmethod
    def from_dict(cls, d):
        return cls(d["block_id"], d["prev_hash"], d["data"], d.get("origin", 0))

    def __hash__(self):
        return hash(self.block_id)

    def __eq__(self, other):
        return isinstance(other, Block) and self.block_id == other.block_id


def broadcast_block_to_neighbors(block: Block, neighbors: List[Tuple[str, int]], seen_blocks: Set[str]):
    def send_to_peer(host, port):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect((host, port))
            msg = json.dumps({"type": "block", "block": block.to_dict()}).encode()
            s.sendall(msg)
            s.close()
        except Exception:
            pass

    for host, port in neighbors:
        threading.Thread(target=send_to_peer, args=(host, port), daemon=True).start()


def send_ack_to_miner(node_id: int, block_id: str, origin: int):
    """把 ACK 直接发回区块 origin 指定的矿工。"""
    miner_ip, miner_port = ipTable[origin]
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect((miner_ip, miner_port))
            msg = json.dumps({
                "type": "ack",
                "from_node": node_id,
                "block_id": block_id,
                "origin": origin
            }).encode()
            s.sendall(msg)
        log(f"[Node {node_id}] Sent ACK for {block_id} to Miner {origin}")
    except Exception as e:
        log(f"[Node {node_id}] Failed to send ACK for {block_id} to Miner {origin}: {e}")


# 节点函数
def run_node(node_id: int, num_nodes: int):
    if not (0 <= node_id < num_nodes):
        raise ValueError(f"node_id must be in [0, {num_nodes - 1}]")
    if not (MIN_NODES <= num_nodes <= MAX_NODES):
        raise ValueError(f"num_nodes must be between {MIN_NODES} and {MAX_NODES}")
    if num_nodes > len(ipTable):
        raise ValueError(f"ipTable only supports up to {len(ipTable)} nodes")

    my_ip, my_port = ipTable[node_id]
    log(f"[Node {node_id}] Starting on {my_ip}:{my_port}")

    # 随机选择约 50% 的其他节点作为邻居，并保证全网连通（环形 + 随机补足）
    other_indices = [i for i in range(num_nodes) if i != node_id]
    num_neighbors = max(1, int(len(other_indices) * 0.5))

    # 环形邻居：每个节点固定连接其后继与前驱，保证网络连通
    ring_indices = set()
    ring_indices.add((node_id + 1) % num_nodes)
    if num_nodes > 2:
        ring_indices.add((node_id - 1) % num_nodes)

    # 从剩余节点中随机补足到目标邻居数量
    remaining = [i for i in other_indices if i not in ring_indices]
    extra_needed = num_neighbors - len(ring_indices)
    if extra_needed > 0:
        extra = random.sample(remaining, k=min(extra_needed, len(remaining)))
        ring_indices.update(extra)

    neighbor_indices = sorted(ring_indices)
    neighbors = [ipTable[i] for i in neighbor_indices]
    log(f"[Node {node_id}] Neighbors: {[f'{ip}:{port}' for ip, port in neighbors]}")

    seen_blocks: Set[str] = set()
    # 本地维护所有已知区块，以 prev_hash 保留分叉结构。
    blockchain: Dict[str, Block] = {}
    chain_lock = threading.Lock()
    running = True

    def chain_height(block_id: str) -> int:
        """根据 prev_hash 计算区块高度；创世标记 0 的高度为 0。"""
        height = 0
        current = block_id
        visited = set()
        while current in blockchain and current not in visited:
            visited.add(current)
            height += 1
            current = blockchain[current].prev_hash
        return height

    def best_tip() -> str:
        """选择最高链末端；高度相同则按区块 ID 确定性选择。"""
        if not blockchain:
            return "0"
        parents = {block.prev_hash for block in blockchain.values()}
        tips = [block_id for block_id in blockchain if block_id not in parents]
        return max(tips, key=lambda bid: (chain_height(bid), bid))

    # listening
    def server_loop():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((my_ip, my_port))
            sock.listen(10)
        except Exception as e:
            log(f"[Node {node_id}] Bind failed: {e}")
            return

        while running:
            try:
                conn, addr = sock.accept()
                data = conn.recv(4096)
                conn.close()
                if data:
                    try:
                        msg = json.loads(data.decode())
                        msg_type = msg.get("type")

                        if msg_type == "block":
                            block = Block.from_dict(msg["block"])
                            with chain_lock:
                                is_new = block.block_id not in seen_blocks
                                if is_new:
                                    seen_blocks.add(block.block_id)
                                    blockchain[block.block_id] = block
                            if is_new:
                                print_block_event(f"[Node {node_id}] Received block:",
                                                  block.block_id, block.origin,
                                                  block.prev_hash, block.data)
                                broadcast_block_to_neighbors(block, neighbors, seen_blocks)
                                if node_id != block.origin:
                                    send_ack_to_miner(node_id, block.block_id, block.origin)

                        elif msg_type == "ack" and node_id in MINER_IDS:
                            from_node = msg.get("from_node")
                            block_id = msg.get("block_id")
                            origin = msg.get("origin")
                            if origin == node_id:
                                log(f"[Miner {node_id}] ACK received from Node {from_node} for block {block_id}")

                    except Exception as e:
                        log(f"[Node {node_id}] Parse error: {e}")
            except Exception:
                continue
        sock.close()

    server_thread = threading.Thread(target=server_loop, daemon=True)
    server_thread.start()

    # 每个矿工每 5 秒挖出一个新区块。
    if node_id in MINER_IDS:
        time.sleep(5)  # 等待其他节点启动
        block_counter = 1

        while running:
            random_data = secrets.token_hex(16)
            with chain_lock:
                prev_hash = best_tip()
                block_id = f"block_{node_id}_{block_counter}"
                new_block = Block(block_id=block_id, prev_hash=prev_hash,
                                  data=random_data, origin=node_id)
                blockchain[block_id] = new_block
                seen_blocks.add(block_id)

            print_block_event(f"[Miner {node_id}] Broadcasting block:",
                              block_id, node_id, prev_hash, random_data)
            broadcast_block_to_neighbors(new_block, neighbors, seen_blocks)

            block_counter += 1
            time.sleep(5)

    try:
        while running:
            time.sleep(1)
    except KeyboardInterrupt:
        log(f"\n[Node {node_id}] Shutting down...")
    finally:
        running = False



def launch_all(num_nodes: int):
    """在单个进程中以线程方式启动全部节点。按 Ctrl+C 停止。"""
    if not (MIN_NODES <= num_nodes <= MAX_NODES):
        raise ValueError(f"num_nodes must be between {MIN_NODES} and {MAX_NODES}")

    log(f"Launching {num_nodes} nodes in one process (press Ctrl+C to stop)...")
    threads = []
    for node_id in range(num_nodes):
        t = threading.Thread(target=run_node, args=(node_id, num_nodes), daemon=True)
        t.start()
        threads.append(t)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("\nShutting down all nodes...")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        # 默认启动全部 6 个节点
        launch_all(6)
    elif len(sys.argv) == 2:
        # 指定节点数量，启动全部节点
        try:
            num_nodes = int(sys.argv[1])
        except ValueError:
            log("Error: num_nodes must be an integer.")
            sys.exit(1)
        try:
            launch_all(num_nodes)
        except ValueError as e:
            log(f"Error: {e}")
            sys.exit(1)
    elif len(sys.argv) == 3:
        # 单节点模式（保留原用法，便于调试）
        try:
            node_id = int(sys.argv[1])
            num_nodes = int(sys.argv[2])
        except ValueError:
            log("Error: Both arguments must be integers.")
            sys.exit(1)
        try:
            run_node(node_id, num_nodes)
        except Exception as e:
            log(f"[Node {node_id}] Fatal error: {e}")
            sys.exit(1)
    else:
        log("Usage:")
        log("  python p2pSim.py                        # 启动默认 6 个节点")
        log("  python p2pSim.py <num_nodes>            # 启动指定数量的节点")
        log("  python p2pSim.py <node_id> <num_nodes>  # 只启动单个节点（调试用）")
        sys.exit(1)