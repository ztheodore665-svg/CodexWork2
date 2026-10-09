# Priests and Devils 2D

Unity 2022.3 2D coursework project for Lab 5.

打开 `Assets/Scenes/Main.unity` 后运行即可开始游戏。角色与 UI 都在运行时动态生成：白色方块是牧师，红色球形是恶魔。点击角色登船/下船，点击 `GO` 让船过河，点击 `RESTART` 重置。

项目采用 MVC：

- `Assets/Scripts/Models/GameModel.cs`：6 个角色、船、倒计时与安全规则。
- `Assets/Scripts/Views/GameView.cs`：运行时生成背景、两岸、水面、船、角色和 HUD。
- `Assets/Scripts/Controllers/GameController.cs`：`LoadResources`、用户操作、船动画和计时。

场景初始只包含 `Main Camera` 和 `GameController`，没有使用 `Find` 或 `SendMessage`。
