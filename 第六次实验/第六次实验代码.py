import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor


class Block:
    """保存区块字段，并通过递增 nonce 搜索满足难度要求的哈希。"""

    def __init__(self, index, timestamp, data, prev_hash):
        self.index = index
        self.timestamp = timestamp
        self.data = data
        self.prev_hash = prev_hash
        self.nonce = 0

    def hash(self):
        """按区块字段顺序拼接文本后计算 SHA-256。"""
        block_content = (
            f"{self.index}{self.timestamp}{self.data}"
            f"{self.prev_hash}{self.nonce}"
        )
        return hashlib.sha256(block_content.encode("utf-8")).hexdigest()

    def mine(self, difficulty):
        """不断尝试 nonce，直到哈希以 difficulty 个 0 开头。"""
        target_prefix = "0" * difficulty
        while True:
            hash_value = self.hash()
            if hash_value.startswith(target_prefix):
                return self.nonce
            self.nonce += 1


# 这些状态只用于记录当前轮次第一个完成有效 PoW 的节点。
winner_lock = threading.Lock()
winner_found = False
winner_name = None
winner_hash = None
winner_block = None


def mine_block(node_id, block, difficulty, round_num):
    """由一个节点挖矿；以锁保证每轮只登记并输出一个获胜者。"""
    global winner_found, winner_name, winner_hash, winner_block

    # 每个节点传入独立的 Block 实例，避免多个线程竞争修改同一个 nonce。
    block.mine(difficulty)
    block_hash = block.hash()

    with winner_lock:
        if not winner_found:
            winner_found = True
            winner_name = f"Node-{node_id}"
            winner_hash = block_hash
            winner_block = block
            print(f"Message: {winner_name} mined the block in Round {round_num}!")
            print(f"Winning Hash: {winner_hash}")


def main():
    # PoW 网络配置
    num_nodes = 5  # 参与节点数量
    num_rounds = 5  # PoW 运行轮次
    difficulty = 4  # 要求哈希以 4 个十六进制 0 开头

    prev_hash = "0" * 64  # 创世区块的前序哈希占位值

    for round_num in range(1, num_rounds + 1):
        print(f"\n--- Round {round_num}: Mining Started ---")

        # Step 1：上一轮的全部线程已结束后，重置本轮获胜状态。
        global winner_found, winner_name, winner_hash, winner_block
        with winner_lock:
            winner_found = False
            winner_name = None
            winner_hash = None
            winner_block = None

        # Step 2：本轮所有节点竞争同一组区块内容，但各自拥有独立 nonce。
        timestamp = int(time.time())
        blocks = [
            Block(
                round_num,
                timestamp,
                f"Round {round_num} transaction data | candidate Node-{node_id}",
                prev_hash,
            )
            for node_id in range(1, num_nodes + 1)
        ]

        # Step 3：提交五个节点的挖矿任务，并等待线程池中的任务全部结束。
        # 获胜者在 mine_block 中通过锁确定。其他已在运行的节点会完成
        # 各自的 nonce 搜索，但不会覆盖获胜区块的哈希或区块对象。
        with ThreadPoolExecutor(max_workers=num_nodes) as executor:
            futures = [
                executor.submit(mine_block, node_id, blocks[node_id - 1], difficulty, round_num)
                for node_id in range(1, num_nodes + 1)
            ]
            for future in futures:
                future.result()

        # 下一轮引用本轮胜出区块的哈希，形成前后区块链接。
        prev_hash = winner_hash
        print(f"Block Hash: {prev_hash}")

    print("\nAll rounds completed.")


if __name__ == "__main__":
    main()
