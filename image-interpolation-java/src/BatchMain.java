import javax.imageio.ImageIO;
import java.awt.image.BufferedImage;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.EnumSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/** 批量处理一个图片目录，并按算法与缩放倍率分目录保存结果。 */
public final class BatchMain {
    private static final Set<String> IMAGE_EXTENSIONS = Set.of(
            "png", "jpg", "jpeg", "bmp", "gif", "tif", "tiff");

    private BatchMain() {
    }

    public static void main(String[] args) throws IOException {
        Options options = Options.parse(args);
        if (options.help) {
            printUsage();
            return;
        }
        if (!Files.isDirectory(options.inputDir)) {
            throw new IOException("输入目录不存在: " + options.inputDir);
        }

        List<Path> images;
        try (var stream = Files.list(options.inputDir)) {
            images = stream.filter(Files::isRegularFile)
                    .filter(BatchMain::isImage)
                    .sorted(Comparator.comparing(path -> path.getFileName().toString()))
                    .toList();
        }
        if (images.isEmpty()) {
            throw new IOException("输入目录中没有可处理的图片: " + options.inputDir);
        }

        Files.createDirectories(options.outputDir);
        System.out.printf(Locale.ROOT, "发现 %d 张图片，输出目录: %s%n",
                images.size(), options.outputDir.toAbsolutePath());

        int success = 0;
        for (Path input : images) {
            BufferedImage source = ImageIO.read(input.toFile());
            if (source == null) {
                System.err.println("跳过无法读取的图片: " + input);
                continue;
            }
            String baseName = removeExtension(input.getFileName().toString());
            System.out.printf(Locale.ROOT, "[%d/%d] %s (%d x %d)%n", success + 1,
                    images.size(), input.getFileName(), source.getWidth(), source.getHeight());

            for (double scale : options.scales) {
                int width = Math.max(1, (int) Math.round(source.getWidth() * scale));
                int height = Math.max(1, (int) Math.round(source.getHeight() * scale));
                for (InterpolationMethod method : options.methods) {
                    long start = System.nanoTime();
                    BufferedImage result = ImageInterpolator.resize(source, width, height, method);
                    Path scaleDirectory = options.outputDir
                            .resolve(method.cliName())
                            .resolve(scaleLabel(scale) + "x");
                    Files.createDirectories(scaleDirectory);
                    Path output = scaleDirectory.resolve(baseName + ".png");
                    ImageIO.write(result, "png", output.toFile());
                    double elapsedMs = (System.nanoTime() - start) / 1_000_000.0;
                    System.out.printf(Locale.ROOT, "    %-8s %-5sx -> %.1f ms%n",
                            method.cliName(), scaleLabel(scale), elapsedMs);
                }
            }
            success++;
        }
        System.out.printf(Locale.ROOT, "批处理完成：成功处理 %d/%d 张图片。%n", success, images.size());
    }

    private static boolean isImage(Path path) {
        String name = path.getFileName().toString();
        int dot = name.lastIndexOf('.');
        return dot >= 0 && IMAGE_EXTENSIONS.contains(name.substring(dot + 1).toLowerCase(Locale.ROOT));
    }

    private static String removeExtension(String name) {
        int dot = name.lastIndexOf('.');
        return dot > 0 ? name.substring(0, dot) : name;
    }

    private static String scaleLabel(double scale) {
        return String.format(Locale.ROOT, "%.2f", scale)
                .replaceAll("0+$", "")
                .replaceAll("\\.$", "");
    }

    private static void printUsage() {
        System.out.println("用法: java BatchMain --input-dir <图片目录> [选项]");
        System.out.println("选项:");
        System.out.println("  --output-dir <目录>                 输出目录，默认 results-src");
        System.out.println("  --scales <倍率列表>                 逗号分隔，默认 0.5,3.0");
        System.out.println("  --methods <方法列表>                逗号分隔，默认 nearest,bilinear,bicubic");
        System.out.println("  --help                              显示帮助");
    }

    private static final class Options {
        private Path inputDir;
        private Path outputDir = Paths.get("results-src");
        private List<Double> scales = List.of(0.5, 3.0);
        private EnumSet<InterpolationMethod> methods = EnumSet.allOf(InterpolationMethod.class);
        private boolean help;

        private static Options parse(String[] args) {
            Options options = new Options();
            for (int i = 0; i < args.length; i++) {
                String arg = args[i];
                switch (arg) {
                    case "--input-dir" -> options.inputDir = Paths.get(requireValue(args, ++i, arg));
                    case "--output-dir" -> options.outputDir = Paths.get(requireValue(args, ++i, arg));
                    case "--scales" -> options.scales = parseScales(requireValue(args, ++i, arg));
                    case "--methods" -> options.methods = parseMethods(requireValue(args, ++i, arg));
                    case "--help", "-h" -> options.help = true;
                    default -> throw new IllegalArgumentException("未知参数: " + arg);
                }
            }
            if (!options.help && options.inputDir == null) {
                throw new IllegalArgumentException("必须通过 --input-dir 指定输入目录");
            }
            return options;
        }

        private static String requireValue(String[] args, int index, String option) {
            if (index >= args.length) {
                throw new IllegalArgumentException(option + " 缺少参数值");
            }
            return args[index];
        }

        private static List<Double> parseScales(String text) {
            List<Double> scales = new ArrayList<>();
            for (String item : text.split(",")) {
                double scale = Double.parseDouble(item.trim());
                if (!(scale > 0.0) || Double.isInfinite(scale)) {
                    throw new IllegalArgumentException("缩放倍率必须是有限正数: " + item);
                }
                scales.add(scale);
            }
            if (scales.isEmpty()) {
                throw new IllegalArgumentException("至少需要一个缩放倍率");
            }
            return scales;
        }

        private static EnumSet<InterpolationMethod> parseMethods(String text) {
            EnumSet<InterpolationMethod> methods = EnumSet.noneOf(InterpolationMethod.class);
            for (String item : text.split(",")) {
                methods.add(InterpolationMethod.fromCliName(item.trim()));
            }
            if (methods.isEmpty()) {
                throw new IllegalArgumentException("至少需要一种插值方法");
            }
            return methods;
        }
    }
}
