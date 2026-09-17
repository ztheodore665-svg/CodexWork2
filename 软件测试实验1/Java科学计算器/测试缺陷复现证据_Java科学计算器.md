# Java 科学计算器缺陷复现证据

测试驱动：`_qa/CalculatorQaHarness.java`。以下结果来自源码重新编译后的 `Calculator.java` 与 `ExpressionParser.java`，不是对旧 `.class` 文件的推断。

| 缺陷 | 复现输入/操作 | 实际结果 | 预期结果 |
|---|---|---|---|
| D-01 | `2+3=`，继续输入 `×4=` | `54` | `20` |
| D-02 | `evaluateExpression("1e-5")` | `7.718281828459045` | `0.00001` |
| D-03 | `evaluateExpression("-2**2")` | `4.0` | `-4.0`（常规数学优先级） |
| D-04 | 角度模式 `evaluateExpression("tan(90)")` | `1.633123935319537E16` | 错误/未定义 |
| D-05 | 输入 `12+3` 后执行 `clearEntry()` | 当前表达式 `0` | 保留前缀并清当前项，或提供明确的 CE 语义 |
| D-06 | `Locale.GERMANY` 下 `formatResult(1.234567890123456E16)` | `1,234568e+16` | 与解析器一致的可复用格式 |
| D-07 | `inputNumber(null)`、`inputFunction(null)`、`inputConstant(null)` | null 被写入或拼接；`inputOperator(null)` 抛 NPE | 可控拒绝，状态保持合法 |
| D-08 | 在窗口焦点下按数字、`+`、`Enter` | 源码无键盘绑定，按钮动作不由键盘触发 | 与鼠标按钮等价地完成计算 |

## 核心驱动摘要

执行摘要为：`passed=9 failed=9`。失败项对应 D-01 至 D-07；D-08 由 GUI 源码走查确认。通过项包括四则优先级、括号、幂、弧度 `sin(π/2)`、`log/ln`、CE 后重新输入、除零可控报错及大写 `E` 科学计数法。
