"""用标准库模拟区块链正常分叉与私有分叉攻击。"""

from dataclasses import dataclass
from itertools import count


@dataclass(frozen=True)
class Block:
    height: int
    miner: str
    previous_hash: str
    hash: str

    def __str__(self) -> str:
        return f"#{self.height} {self.hash}（{self.miner}）"


_hash_ids = count(1)


def make_block(parent: Block | None, miner: str) -> Block:
    """创建演示用区块；哈希用唯一编号表示，不进行真实 PoW 计算。"""
    height = 0 if parent is None else parent.height + 1
    previous_hash = "0" if parent is None else parent.hash
    block_hash = f"blk_{height}_{next(_hash_ids):04d}"
    return Block(height, miner, previous_hash, block_hash)


def extend(chain: list[Block], miner: str) -> list[Block]:
    return [*chain, make_block(chain[-1], miner)]


def common_prefix_length(first: list[Block], second: list[Block]) -> int:
    length = 0
    for left, right in zip(first, second):
        if left.hash != right.hash:
            break
        length += 1
    return length


def show_chain(label: str, chain: list[Block]) -> None:
    print(f"{label}（长度 {len(chain)}，末高度 #{chain[-1].height}）")
    print("  " + " -> ".join(str(block) for block in chain))


def resolve_fork(chains: list[list[Block]]) -> tuple[list[Block], list[list[Block]]]:
    """按最长链规则选主链，并返回各条未胜出分支。"""
    winner = max(chains, key=len)
    return winner, [chain for chain in chains if chain is not winner]


def normal_fork_demo() -> None:
    print("=== 一、正常分叉 ===")
    chain = [make_block(None, "Genesis")]
    chain = extend(chain, "Alice")
    chain = extend(chain, "Alice")
    show_chain("分叉前的共同主链", chain)

    bob_chain = extend(chain, "Bob")
    carol_chain = extend(chain, "Carol")
    print("\nBob 和 Carol 几乎同时基于同一个末区块挖出新区块：")
    show_chain("Bob 分支", bob_chain)
    show_chain("Carol 分支", carol_chain)

    bob_chain = extend(bob_chain, "Dave")
    print("\nDave 接着 Bob 的区块挖出新区块，Bob 分支变长：")
    show_chain("Bob 分支", bob_chain)
    show_chain("Carol 分支", carol_chain)

    winner, _ = resolve_fork([bob_chain, carol_chain])
    prefix = common_prefix_length(bob_chain, carol_chain)
    stale = carol_chain[prefix:]
    print("\n最长链成为主链：")
    show_chain("最终主链", winner)
    print("被废弃的陈旧区块：" + "、".join(str(block) for block in stale))


def fork_attack_demo() -> None:
    print("\n=== 二、模拟分叉攻击（私有链追赶并超越） ===")
    public_chain = [make_block(None, "Genesis")]
    public_chain = extend(public_chain, "Honest-A")
    public_chain = extend(public_chain, "Honest-B")
    fork_point = public_chain[-1]
    print("攻击者从公共链末端开始秘密挖矿；其间诚实矿工继续公开出块。")

    public_chain = extend(public_chain, "Honest-C")
    public_chain = extend(public_chain, "Honest-D")
    private_chain = [*public_chain[: fork_point.height + 1]]
    private_chain = extend(private_chain, "Attacker-1")
    private_chain = extend(private_chain, "Attacker-2")
    private_chain = extend(private_chain, "Attacker-3")

    print("\n攻击者公布私有分支前：")
    show_chain("诚实矿工的公开链", public_chain)
    show_chain("攻击者的私有链", private_chain)
    print("攻击分叉点：" + str(fork_point))

    winner, _ = resolve_fork([public_chain, private_chain])
    prefix = common_prefix_length(public_chain, private_chain)
    stale = public_chain[prefix:]
    print("\n攻击者公布更长的分支后，节点按最长链规则切换主链：")
    show_chain("最终主链", winner)
    print("被回滚并废弃的诚实链区块：" + "、".join(str(block) for block in stale))
    print("说明：此处只演示链选择与重组过程，不计算工作量证明，也不模拟网络传播或双花交易。")


def main() -> None:
    normal_fork_demo()
    fork_attack_demo()


if __name__ == "__main__":
    main()
