# Codex 工作区归档规则

## GitHub 归档仓库

- 归档仓库：`ztheodore665-svg/CodexWork2`
- 远程地址：`git@github-ztheodore:ztheodore665-svg/CodexWork2.git`
- 统一使用 SSH 主机别名：`github-ztheodore`

## 分支规则

- 每个工作区使用独立分支，格式为 `workspace/<工作区名称>`。
- 同一工作区后续工作继续使用原分支，不重复创建。
- `D:\Codexwork` 作为归档工作区，默认使用 `main` 分支。
- 未经用户明确要求，不直接 push 到 `main`。

## 工作流程

1. 开始工作前同步当前工作区对应的远程分支。
2. 完成工作后，将成果归档到 `D:\Codexwork` 的对应目录。
3. 创建 Git commit 并 push 到当前工作区对应分支。
4. 回复中注明仓库、工作区、分支和 commit ID。
5. 遇到冲突、认证或网络错误时，不覆盖远程内容，先报告问题。

## 分支示例

```text
D:\ProjectA  -> workspace/project-a
D:\ProjectB  -> workspace/project-b
D:\Codexwork -> main
```
