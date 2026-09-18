import javax.imageio.ImageIO;
import java.awt.image.BufferedImage;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.text.DecimalFormat;
import java.util.ArrayList;
import java.util.EnumSet;
import java.util.List;
import java.util.Locale;

/** 命令行入口：将一张实际图片用三种插值方法缩小 0.5 倍、放大 3 倍。 */
public final class Main {
    private static final DecimalFormat SCALE_FORMAT = new DecimalFormat("0.##");

    private Main() {
    }

    public static void main(String[] args) throws IOException {
        Options options = Options.parse(args);
        if (options.help) {
            printUsage();
            return;
        }
        if (options.input == null) {
            throw new IllegalArgumentException("必须通过 --input 指定输入图片");
        }

        BufferedImage source = ImageIO.read(options.input.toFile());
        if (source == null) {
            throw new IOException("无法读取图片，格式可能不受 ImageIO 支持: " + options.input);
        }
        Files.createDirectories(options.outputDir);
        System.out.printf(Locale.ROOT, "输入: %s (%d x %d)%n", options.input,
                source.getWidth(), source.getHeight());

        for (double scale : options.scales) {
            int width = Math.max(1, (int) Math.round(source.getWidth() * scale));
            int height = Math.max(1, (int) Math.round(source.getHeight() * scale));
            for (InterpolationMethod method : options.methods) {
                long start = System.nanoTime();
                BufferedImage result = ImageInterpolator.resize(source, width, height, method);
                String scaleLabel = SCALE_FORMAT.format(scale).replace(',', '.');
                Path output = options.outputDir.resolve(method.cliName() + "_" + scaleLabel + "x.png");
                ImageIO.write(result, "png", output.toFile());
                double elapsedMs = (System.nanoTime() - start) / 1_000_000.0;
                System.out.printf(Locale.ROOT, "%-8s %-5sx -> %s (%d x %d, %.1f ms)%n",
                        method.cliName(), scaleLabel, output, width, height, elapsedMs);
            }
        }
    }

    private static void printUsage() {
        System.out.println("用法: java Main --input <图片> [选项]");
        System.out.println("选项:");
        System.out.println("  --output-dir <目录>                 输出目录，默认 results");
        System.out.println("  --scales <倍率列表>                 逗号分隔，默认 0.5,3.0");
        System.out.println("  --methods <方法列表>                逗号分隔，默认 nearest,bilinear,bicubic");
        System.out.println("  --help                              显示帮助");
    }

    private static final class Options {
        private Path input;
        private Path outputDir = Paths.get("results");
        private List<Double> scales = List.of(0.5, 3.0);
        private EnumSet<InterpolationMethod> methods = EnumSet.allOf(InterpolationMethod.class);
        private boolean help;

        private static Options parse(String[] args) {
            Options options = new Options();
            for (int i = 0; i < args.length; i++) {
                String arg = args[i];
                switch (arg) {
                    case "--input" -> options.input = Paths.get(requireValue(args, ++i, arg));
                    case "--output-dir" -> options.outputDir = Paths.get(requireValue(args, ++i, arg));
                    case "--scales" -> options.scales = parseScales(requireValue(args, ++i, arg));
                    case "--methods" -> options.methods = parseMethods(requireValue(args, ++i, arg));
                    case "--help", "-h" -> options.help = true;
                    default -> throw new IllegalArgumentException("未知参数: " + arg);
                }
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
