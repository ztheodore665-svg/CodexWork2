"""
ethSim.py —— 模拟实现以太坊的账户模型与交易(含 nonce 防重放)

本程序模拟以太坊的外部账户(EOA)模型与转账交易:
  - 账户 Account:balance(余额,单位 Wei)+ nonce(已发起的交易数)
  - 交易 Transaction:from / to / value / nonce
  - 世界状态:一个"地址 -> 账户"的字典,记录所有账户的最新状态

直接运行:
    python ethSim.py

代码中已用 TODO 标注需要你完成的扩展点,搜索 TODO 即可定位。

背景知识:
  - 1 Ether = 10^18 Wei(Wei 是以太币的最小单位)。
  - 以太坊采用"账户模型"(类似银行账户),与比特币的 UTXO 模型不同:
    账户里直接记录余额,转账时校验余额是否足够、更新双方余额。
  - nonce 是账户自创建以来累计发起的交易数,用来防止"重放攻击":
    一笔已签名的交易若被攻击者重复提交,会因为 nonce 已过期而被拒绝。
"""

from typing import Dict


# ============ 账户结构 ============

class Account:
    """以太坊外部账户(EOA):记录该账户的余额与已发起的交易数。"""

    def __init__(self, address: str, balance: int = 0):
        self.address = address          # 账户地址(模拟中简化为字符串,如 "alice")
        self.balance = balance          # 账户余额,单位 Wei
        self.nonce = 0                   # 下一笔有效交易的 nonce,从 0 开始


# ============ 交易结构 ============

class Transaction:
    """一笔转账交易:记录发送者、接收者、金额与 nonce。"""

    def __init__(self, from_addr: str, to_addr: str, value: int, nonce: int):
        self.from_addr = from_addr      # 发送者地址
        self.to_addr = to_addr          # 接收者地址
        self.value = value              # 转账金额,单位 Wei
        self.nonce = nonce              # 发送者发出的第 nonce 笔交易(从 0 开始计数)


# ============ 世界状态(地址 -> 账户) ============

def make_genesis_state() -> Dict[str, Account]:
    """初始化创世状态:alice 持有 100 Wei,bob 持有 10 Wei。"""
    return {
        "alice": Account("alice", 100),
        "bob": Account("bob", 10),
    }


# ============ 交易执行(核心,含 TODO) ============

def apply_transaction(state: Dict[str, Account], tx: Transaction) -> bool:
    """
    执行一笔转账交易,返回 True 表示成功,False 表示被拒绝。

    注意:一旦返回 False,交易不应改变任何账户状态。
    """
    sender = state.get(tx.from_addr)
    receiver = state.get(tx.to_addr)
    if sender is None or receiver is None:
        print(f"  [拒绝] 账户不存在:from={tx.from_addr}, to={tx.to_addr}")
        return False

    if tx.value <= 0:
        print(f"  [拒绝] 转账金额必须为正数:value={tx.value}")
        return False
    if tx.nonce != sender.nonce:
        print(f"  [拒绝] nonce 不匹配:交易 nonce={tx.nonce},账户 nonce={sender.nonce}")
        return False
    if sender.balance < tx.value:
        print(f"  [拒绝] 余额不足:账户余额={sender.balance} Wei,转账金额={tx.value} Wei")
        return False

    # 执行转账(状态转换):扣减发送者、增加接收者
    sender.balance -= tx.value
    receiver.balance += tx.value
    sender.nonce += 1

    print(f"  [成功] {tx.from_addr} -> {tx.to_addr} 转账 {tx.value} Wei (nonce={tx.nonce})")
    return True


# ============ 打印状态 ============

def print_state(state: Dict[str, Account], title: str = ""):
    """打印当前世界状态(各账户余额与 nonce)。"""
    if title:
        print(f"\n=== {title} ===")
    for addr, acc in state.items():
        # 完成实验任务1之前,账户还没有 nonce 字段,这里用 "?" 占位显示。
        nonce = getattr(acc, "nonce", "?")
        print(f"  {addr:<8} balance={acc.balance:>5} Wei   nonce={nonce}")


# ============ 演示场景 ============

def main():
    state = make_genesis_state()
    print_state(state, "创世状态")

    # 场景 1:一次合法的转账
    print("\n>>> 场景 1:alice 向 bob 转账 30 Wei(nonce=0)")
    tx1 = Transaction("alice", "bob", 30, nonce=0)
    apply_transaction(state, tx1)
    print_state(state, "执行 tx1 后")

    # 场景 2:重放攻击 —— 攻击者把 tx1 原样再提交一次
    print("\n>>> 场景 2:攻击者重放 tx1(同样的 nonce=0,再转 30 Wei)")
    apply_transaction(state, tx1)   # 期望:被 nonce 校验拒绝(完成实验任务3后)
    print_state(state, "重放 tx1 后")

    # 场景 3:alice 正常发起第二笔交易(nonce=1)
    print("\n>>> 场景 3:alice 正常发起第二笔交易 nonce=1")
    tx2 = Transaction("alice", "bob", 20, nonce=1)
    apply_transaction(state, tx2)
    print_state(state, "执行 tx2 后")

    # 场景 4:余额不足
    print("\n>>> 场景 4:bob 尝试转账 1000 Wei(余额不足)")
    tx3 = Transaction("bob", "alice", 1000, nonce=0)
    apply_transaction(state, tx3)   # 期望:被余额校验拒绝(完成实验任务2后)
    print_state(state, "执行 tx3 后")


if __name__ == "__main__":
    main()
