# 牧师与恶魔 2D

这是 3D 游戏编程实验五的 Unity 2D 实现。运行时只保留 `Main Camera` 与 `GameController` 两个场景对象，其余游戏对象由 `GameController.LoadResources()` 和 `GameView` 动态生成。

## 操作

| 操作 | 作用 |
| --- | --- |
| 点击白色方块 | 选择/取消选择牧师登船 |
| 点击红色球 | 选择/取消选择恶魔登船 |
| GO | 船载 1~2 人往返，船移动期间不能操作角色 |
| RESTART | 重置 3 牧师、3 恶魔和 120 秒倒计时 |

## 规则

- 任何一岸只要有牧师，恶魔数量就不能超过牧师数量。
- 船每次最多 2 人，且至少 1 人掌舵。
- 两岸都安全并且 6 人到达右岸时获胜。

## 结构

- `Models/GameModel.cs`：状态、集合和安全规则。
- `Views/GameView.cs`：动态创建背景、岸、船、人物和 HUD。
- `Views/PersonView.cs`：仅转发点击意图，不直接修改模型。
- `Controllers/GameController.cs`：场景控制器、资源初始化、动画和计时。
