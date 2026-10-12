import hashlib
import json
from typing import List, Dict, Any
from dataclasses import dataclass
from enum import Enum
import coincurve  # pip install coincurve

# =======================
# 工具函数
# =======================

def hash160(data: bytes) -> bytes:
    """RIPEMD160(SHA256(data)) 用于生成 P2PKH 地址的哈希部分"""
    sha256 = hashlib.sha256(data).digest()
    r160 = hashlib.new('ripemd160', sha256).digest()
    return r160

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

# 生成密钥对
def generate_key_pair() -> tuple[str, str]:
    """生成比特币风格的密钥对 (私钥 hex, 公钥 hex)"""
    private_key = coincurve.PrivateKey()
    public_key = private_key.public_key.format(compressed=True)  # 33 bytes, compressed
    return private_key.to_hex(), public_key.hex()

# ECDSA 签名：私钥 + 交易哈希 --> 签名
def sign(tx_hash: str, private_key_hex: str) -> str:
    """使用私钥对交易哈希进行 ECDSA 签名"""
    private_key_bytes = bytes.fromhex(private_key_hex)
    private_key = coincurve.PrivateKey(private_key_bytes)
    message_hash = bytes.fromhex(tx_hash)
    signature = private_key.sign(message_hash, hasher=None)  # 不额外哈希，传入已哈希的数据
    return signature.hex()

# ECDSA 验证：公钥 + 交易哈希 + 签名 --> bool
# 模拟 OP_CHECKSIG
def verify_signature(sig_hex: str, tx_hash: str, public_key_hex: str) -> bool:
    """使用公钥验证签名"""
    try:
        message_hash = bytes.fromhex(tx_hash)
        signature = bytes.fromhex(sig_hex)
        public_key = coincurve.PublicKey(bytes.fromhex(public_key_hex))
        return public_key.verify(signature, message_hash, hasher=None)
    except Exception:
        return False

# Base58Check 编码简化版
def pubkey_to_p2pkh(pubkey_hex: str) -> str:
    """输入公钥 hex 字符串，返回模拟 P2PKH 地址"""
    pubkey_bytes = bytes.fromhex(pubkey_hex)
    h160 = hash160(pubkey_bytes).hex()
    return f"1{h160[:32]}"  # 模拟以 1 开头的地址


# =======================
# 枚举和数据结构
# =======================

class ScriptType(Enum):
    P2PKH = "P2PKH"

@dataclass
class TxIn:             # 交易输入，指向一个 UTXO
    txid: str           # 引用前一个交易 ID
    vout: int           # 输出索引
    script_sig: dict    # 解锁脚本 { 'signature': ..., 'pubkey': ... }
    sequence: int = 0xFFFFFFFF

@dataclass
class TxOut:                        # 交易输出，定义了“谁可以花这笔钱”
    value: float                    # BTC 数量
    script_pubkey: Dict[str, Any]   # 锁定脚本

    def to_scriptpubkey_str(self) -> str:
        t = self.script_pubkey["type"]
        if t == ScriptType.P2PKH:
            return f"P2PKH:{self.script_pubkey['hash']}"
        return "Unknown"

@dataclass
class Transaction:
    txid: str                   # 整个交易的 SHA256 哈希，唯一标识一笔交易，在 TxIn 中引用前一个 UTXO
    inputs: List[TxIn]
    outputs: List[TxOut]

    def get_hash(self) -> str:
        data = {
            "inputs": [
                {
                    "txid": txin.txid,
                    "vout": txin.vout,
                    "script_sig": txin.script_sig,
                    "sequence": txin.sequence
                }
                for txin in self.inputs
            ],
            "outputs": [
                {
                    "value": out.value,
                    "script_pubkey": {
                        "type": out.script_pubkey["type"].value,  # Enum → string
                        "hash": out.script_pubkey["hash"]
                    }
                }
                for out in self.outputs
            ]
        }
        serialized = json.dumps(data, sort_keys=True)
        return hashlib.sha256(serialized.encode()).hexdigest()

    def get_sighash(self, input_index: int, script_code: Dict[str, Any]) -> str:
        """计算待签名的哈希（sighash）。

        真实比特币在签名时，会把「本笔交易」序列化，其中：
        - 被签名的那个输入，其 scriptSig 被替换为它所花费 UTXO 的 scriptCode（P2PKH 中即 scriptPubKey）；
        - 其余输入的 scriptSig 置空；
        然后对整体序列化结果做哈希（这里用 SHA256 简化，真实实现为 Double-SHA256 并附加 4 字节 sighash 类型）。
        """
        inputs = []
        for i, txin in enumerate(self.inputs):
            if i == input_index:
                # 用被花费 UTXO 的 scriptCode 替换 scriptSig
                script_sig = {
                    "type": script_code["type"].value,  # Enum → string
                    "hash": script_code["hash"]
                }
            else:
                script_sig = ""  # 其他输入的 scriptSig 置空
            inputs.append({
                "txid": txin.txid,
                "vout": txin.vout,
                "script_sig": script_sig,
                "sequence": txin.sequence
            })
        outputs = [
            {
                "value": out.value,
                "script_pubkey": {
                    "type": out.script_pubkey["type"].value,  # Enum → string
                    "hash": out.script_pubkey["hash"]
                }
            }
            for out in self.outputs
        ]
        data = {"inputs": inputs, "outputs": outputs}
        serialized = json.dumps(data, sort_keys=True)
        return hashlib.sha256(serialized.encode()).hexdigest()

    def verify(self, utxo_set: Dict[str, TxOut]) -> bool:
        """验证整笔交易"""
        input_sum = 0.0
        for i, txin in enumerate(self.inputs):
            prev_out_id = f"{txin.txid}:{txin.vout}"
            if prev_out_id not in utxo_set:
                print(f"❌ UTXO {prev_out_id} 不存在或已被花费")
                return False

            prev_out = utxo_set[prev_out_id]
            input_sum += prev_out.value

            # 验证脚本（P2PKH）
            if not verify_p2pkh_script(self, i, txin, prev_out):
                print(f"❌ 脚本验证失败 for input {prev_out_id}")
                return False

        output_sum = sum(out.value for out in self.outputs)
        if output_sum > input_sum:
            print(f"❌ 输出超过输入：输出 {output_sum}, 输入 {input_sum}")
            return False

        if input_sum - output_sum > 0.01:  # 简单手续费检查
            print(f"⚠️  手续费过高: {input_sum - output_sum:.4f} BTC")

        return True


# =======================
# 脚本执行模拟（P2PKH）
# =======================

def verify_p2pkh_script(tx: Transaction, input_index: int, txin: TxIn, prev_out: TxOut) -> bool:
    script_pubkey = prev_out.script_pubkey
    if script_pubkey["type"] != ScriptType.P2PKH:
        return False

    sig = txin.script_sig.get("signature")
    pubkey_hex = txin.script_sig.get("pubkey")
    pubkey_bytes = bytes.fromhex(pubkey_hex)
    pubkey_hash = hash160(pubkey_bytes).hex()

    # 1. 公钥哈希匹配
    # 模拟 OP_EQUALVERIFY
    if pubkey_hash != script_pubkey["hash"]:
        print(f"公钥哈希不匹配: {pubkey_hash} != {script_pubkey['hash']}")
        return False

    # 2. 验证签名（使用真实的 ECDSA 验证）
    # 签名的是本笔交易的 sighash：把被花费 UTXO 的 scriptCode 代回 scriptSig 后整体哈希
    sighash = tx.get_sighash(input_index, script_pubkey)
    if not verify_signature(sig, sighash, pubkey_hex):
        print(f"签名验证失败: {sig} 不匹配 {pubkey_hex}")
        return False

    return True


# =======================
# 主流程：A 向 B 转账 5 BTC
# =======================

def main():
    print("🚀 开始模拟 BTC 转账（基于 UTXO + P2PKH）\n")

    # ===== 1. 生成密钥对: 为 A 与 B 生成必要的 <pk,sk> and their addresses =====
    A_private_key, A_public_key = generate_key_pair()
    B_private_key, B_public_key = generate_key_pair()

    A_address = pubkey_to_p2pkh(A_public_key)
    B_address = pubkey_to_p2pkh(B_public_key)

    print(f"A 的地址: {A_address}")
    print(f"B 的地址: {B_address}\n")

 
    # ===== 2. 初始化“全局 UTXO 集”: 首先创建 A 的交易的 UTXO (包含10个 BTC)  =====
    # ===== 创建一个属于 A 的 UTXO（内含10个 BTC），被 A 的 pubkey_hash 锁定 =====
    genesis_txid = "0000000000000000000000000000000000000000000000000000000000000001"
    utxo_set = {            
        f"{genesis_txid}:0": TxOut(
            value=10.0,
            script_pubkey={
                "type": ScriptType.P2PKH,
                "hash": hash160(bytes.fromhex(A_public_key)).hex()
            }
        )
    }
    print(f"初始“全局 UTXO 集”: 当前总共有 {len(utxo_set)} 个【输出】")
    for k, v in utxo_set.items():
        print(f"  {k} -> {v.value} BTC, {v.to_scriptpubkey_str()}")



    # ===== 3. 构造一笔转账交易 tx:< A → B, 5> =====
    print("\n构造一个转账交易 tx: A → B (5 BTC)")

    # Step I: 构建交易的【输入】txin：消费 A 的 UTXO 的 Output
    # 先创建一个“未签名”的输入，script_sig 暂时为空，等算出 sighash 后再填
    txin = TxIn(
        txid=genesis_txid,
        vout=0,
        script_sig={}
    )


    # Step II: 构造该交易的【输出】 out1：支付给 B 5 个 BTC
    # 把 BTC “锁定” 到一个 “锁定脚本” pubkey_hash 上
    # 在比特币的 P2PKH 脚本模式中，这个 script_pubkey 会被编码为：OP_DUP OP_HASH160 <pubkey_hash> OP_EQUALVERIFY OP_CHECKSIG
    out1 = TxOut(
        value=5.0,
        script_pubkey={
            "type": ScriptType.P2PKH,
            "hash": hash160(bytes.fromhex(B_public_key)).hex()
        }
    )


    # Step III: 构造该交易的【输出】 out2：找零给 A (10 - 5 - 0.01 fee = 4.99 BTC)
    out2 = TxOut(
        value=4.99,
        script_pubkey={
            "type": ScriptType.P2PKH,
            "hash": hash160(bytes.fromhex(A_public_key)).hex()
        }
    )


    # Step IV: 创建该“本笔交易” raw_tx
    raw_tx = Transaction(
        txid="",
        inputs=[txin],
        outputs=[out1, out2]
    )

    # Step V: 计算 sighash 并签名（真实比特币的“签名”签的是 sighash）
    # 被花费 UTXO 的【锁定脚本】即 scriptCode（P2PKH 中即 scriptPubKey）
    # 提示：先用 raw_tx.get_sighash(input_index=0, script_code=被花费 UTXO 的 script_pubkey) 算出 sighash，
    #       再 sign(sighash, A_private_key) 得到签名，然后把 <signature> <pubkey> 填入 txin.script_sig，
    #       最后再计算 txid。
    prev_script_code = utxo_set[f"{txin.txid}:{txin.vout}"].script_pubkey
    sighash = raw_tx.get_sighash(input_index=0, script_code=prev_script_code)
    signature = sign(sighash, A_private_key)

    # 把签名和公钥填入【解锁脚本】script_sig（<signature> <pubkey>）
    txin.script_sig = {
        "signature": signature,
        "pubkey": A_public_key
    }

    # 现在“本笔交易” raw_tx 才最终确定，计算它的 txid
    raw_tx.txid = raw_tx.get_hash() ## 请同学们补全这部分代码； #用意：计算 txid 

    print(f"\n📝 展示该交易的详情:")
    print(f"  TXID: {raw_tx.txid}")
    print(f"  该交易的【输入】: 1 个 (消费了 {txin.txid}:{txin.vout})")
    print(f"  该交易的【输出】: 2 个")
    print(f"    → {out1.value} 个 BTC 支付给了 {B_address}")
    print(f"    → {out2.value} 个 BTC 找零给了 {A_address}")
    print(f"  该交易的手续费: {10 - 5 - 4.99:.4f} 个 BTC")


    # ===== 4. 模拟 (其他关心该交易的共识节点 someone) 验证该交易 =====
    print("\n🔍 Someone 正在验证交易...")
    is_valid = raw_tx.verify(utxo_set)
    if not is_valid:
        raise RuntimeError("交易验证失败，停止更新 UTXO 集")
    print("✅ 交易验证通过！")



    # ===== 5. 更新“全局 UTXO 集” =====
    print("\n✅ 该交易上链时，需要更新“全局 UTXO 集”...")
    # 移除已花费的 UTXO
    spent_key = f"{genesis_txid}:0"
    del utxo_set[spent_key]

    # 添加新 UTXO
    for i, out in enumerate(raw_tx.outputs):
        utxo_set[f"{raw_tx.txid}:{i}"] = out

    print(f"新的“全局 UTXO 集”: 总共有 {len(utxo_set)} 个输出")
    for k, v in utxo_set.items():
        print(f"  {k} -> {v.value} BTC, {v.to_scriptpubkey_str()}")

    print("\n🎉 模拟完成！")


if __name__ == "__main__":
    main()



############# 2026Oct12, 黄华威, 作业要求：############# 
# 这是本周实验课程的作业，需要大家在 main 函数中补充部分代码，使得该程序能够成功执行 UTXO 交易的 P2PKH 模式，并(让 someone)验证该交易的合法性。
# 作业提交内容：（1）补充完整的 python 文件；（2）执行结果图文记录到一个 PDF 文档中。
############# ############# ############# ############# 


