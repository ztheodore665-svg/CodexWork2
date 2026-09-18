# Java 图像插值实验

本项目完成作业要求的三种图像插值算法：

- 最近邻插值（Nearest Neighbor）
- 双线性插值（Bilinear）
- 双三次插值（Bicubic，Catmull-Rom 核）

程序使用 Java 标准库 `BufferedImage` 和 `ImageIO`，可以直接处理 PNG、JPG、BMP 等常见格式，不依赖 OpenCV 或其他第三方库。

## 编译与运行

需要 JDK 17 或更高版本。在 PowerShell 中执行：

```powershell
.\run.ps1 -InputImage ".\input\sample.png"
```

默认会在 `results/` 生成 6 张结果图：每种算法各输出 0.5 倍和 3 倍两种尺寸。

如果要一次处理上级 `src` 文件夹中的全部图片，可以执行：

```powershell
.\batch.ps1
```

批处理结果按算法和倍率分目录保存，例如：

```text
results-src/
├─ nearest/
│  ├─ 0.5x/cameraman.png
│  └─ 3x/cameraman.png
├─ bilinear/
│  ├─ 0.5x/cameraman.png
│  └─ 3x/cameraman.png
└─ bicubic/
   ├─ 0.5x/cameraman.png
   └─ 3x/cameraman.png
```

程序会识别 PNG、JPG、BMP、GIF、TIF 和 TIFF 文件；输出统一使用 PNG，避免 JPEG 有损压缩影响算法对比。

本项目同时提供 Word 格式实验报告：`实验报告_代码与结果.docx`。报告只介绍代码文件和 `src` 图片批处理结果，并插入 `cameraman` 的六张代表性结果图。

也可以手动编译并指定参数：

```powershell
javac -encoding UTF-8 -d out src\*.java
java -cp out Main --input input\sample.png --output-dir results `
  --scales 0.5,3.0 --methods nearest,bilinear,bicubic
```

单独运行基础测试：

```powershell
java -cp out InterpolationTest
```

输出文件命名规则为 `<方法>_<倍率>x.png`，例如 `bicubic_3x.png`。

## 生成提交压缩包

作业要求的文件名包含学号和姓名，可在 PowerShell 中执行：

```powershell
.\package.ps1 -StudentId "你的学号" -StudentName "你的姓名"
```

脚本会生成 `第一作业_你的学号_你的姓名.zip`，压缩包内包含源代码、实验报告、示例输入图和已生成结果图。

## 算法说明

输出像素先通过

`x = x_out × (W_in - 1) / (W_out - 1)`，
`y = y_out × (H_in - 1) / (H_out - 1)`

映射回输入图像的连续坐标。这样可以保证输出图像边界与输入图像边界对齐。边界采样采用钳位策略。

最近邻只取距离最近的一个像素；双线性使用周围 2×2 像素做加权平均；双三次使用周围 4×4 像素和 Catmull-Rom 三次核进行加权。双三次计算后会把通道值限制在 0 到 255，避免振铃造成的越界颜色。
