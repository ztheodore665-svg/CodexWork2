# 第四次实验：对 BTC 所有权的确认

本目录实现实验 PDF 第 42-43 页要求的四个步骤：

1. `NewWallet()` 为 Alice 和 Bob 生成 secp256k1 密钥对以及 Bitcoin 风格地址。
2. Alice 构造 `TransferData{To, Amount}`，其中 `To` 为 Bob 的地址。
3. Alice 使用 `Sign(alice.PrivateKey, transfer.Message())` 对交易消息签名。
4. 共识节点使用 `Verify(alice.PublicKey, transfer.Message(), sig)` 验证 Alice 是否拥有转出地址对应的私钥。

代码使用 Go 1.23+，依赖 `decred/dcrd` 的 secp256k1 实现和 `x/crypto` 的 RIPEMD-160。地址编码为 Base58Check；签名为 SHA-256 摘要上的 DER 编码 ECDSA 签名。

## 运行

```powershell
go test -v
go run .
```

`go run .` 会打印一次完整的 Alice -> Bob 转账示例，并额外验证篡改金额和错误公钥两种失败场景。

## 文件说明

- `wallet.go`：钱包、地址、交易消息、签名和验签实现。
- `main.go`：实验演示程序。
- `wallet_test.go`：正确签名、篡改交易、错误公钥、非法签名、地址校验测试。
- `report/build_report.py`：实验报告 PDF 生成脚本。
- `output/experiment_report.pdf`：完成版实验报告。
