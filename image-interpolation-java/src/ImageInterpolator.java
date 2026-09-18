import java.awt.image.BufferedImage;

/**
 * 使用 Java 标准库实现的三种二维图像插值。
 * 坐标映射采用 align-corners 规则，使输出图像四个边界与输入图像边界对齐。
 */
public final class ImageInterpolator {
    private ImageInterpolator() {
    }

    public static BufferedImage resize(BufferedImage source, int targetWidth, int targetHeight,
                                       InterpolationMethod method) {
        if (source == null) {
            throw new IllegalArgumentException("输入图像不能为 null");
        }
        if (targetWidth < 1 || targetHeight < 1) {
            throw new IllegalArgumentException("目标宽度和高度必须大于 0");
        }

        BufferedImage result = new BufferedImage(targetWidth, targetHeight,
                BufferedImage.TYPE_INT_ARGB);
        for (int y = 0; y < targetHeight; y++) {
            double sourceY = mapCoordinate(y, targetHeight, source.getHeight());
            for (int x = 0; x < targetWidth; x++) {
                double sourceX = mapCoordinate(x, targetWidth, source.getWidth());
                int argb;
                switch (method) {
                    case NEAREST -> argb = nearest(source, sourceX, sourceY);
                    case BILINEAR -> argb = bilinear(source, sourceX, sourceY);
                    case BICUBIC -> argb = bicubic(source, sourceX, sourceY);
                    default -> throw new IllegalStateException("未知插值方法: " + method);
                }
                result.setRGB(x, y, argb);
            }
        }
        return result;
    }

    private static double mapCoordinate(int destinationIndex, int destinationSize,
                                        int sourceSize) {
        if (destinationSize == 1 || sourceSize == 1) {
            return 0.0;
        }
        return (double) destinationIndex * (sourceSize - 1) / (destinationSize - 1);
    }

    private static int nearest(BufferedImage image, double x, double y) {
        int ix = clamp((int) Math.floor(x + 0.5), 0, image.getWidth() - 1);
        int iy = clamp((int) Math.floor(y + 0.5), 0, image.getHeight() - 1);
        return image.getRGB(ix, iy);
    }

    private static int bilinear(BufferedImage image, double x, double y) {
        int x0 = (int) Math.floor(x);
        int y0 = (int) Math.floor(y);
        int x1 = Math.min(x0 + 1, image.getWidth() - 1);
        int y1 = Math.min(y0 + 1, image.getHeight() - 1);
        double dx = x - x0;
        double dy = y - y0;

        int top = interpolateArgb(image.getRGB(x0, y0), image.getRGB(x1, y0), dx);
        int bottom = interpolateArgb(image.getRGB(x0, y1), image.getRGB(x1, y1), dx);
        return interpolateArgb(top, bottom, dy);
    }

    private static int bicubic(BufferedImage image, double x, double y) {
        int xBase = (int) Math.floor(x);
        int yBase = (int) Math.floor(y);
        double dx = x - xBase;
        double dy = y - yBase;
        int[] channels = new int[4];

        for (int channel = 0; channel < 4; channel++) {
            double value = 0.0;
            for (int row = -1; row <= 2; row++) {
                double rowValue = 0.0;
                for (int column = -1; column <= 2; column++) {
                    int sample = channel(image, xBase + column, yBase + row, channel);
                    rowValue += sample * cubicWeight(column - dx);
                }
                value += rowValue * cubicWeight(row - dy);
            }
            channels[channel] = clamp((int) Math.round(value), 0, 255);
        }
        return argb(channels[0], channels[1], channels[2], channels[3]);
    }

    /** Catmull-Rom 双三次核，参数 a=-0.5。 */
    private static double cubicWeight(double distance) {
        double abs = Math.abs(distance);
        if (abs <= 1.0) {
            return 1.5 * abs * abs * abs - 2.5 * abs * abs + 1.0;
        }
        if (abs < 2.0) {
            return -0.5 * abs * abs * abs + 2.5 * abs * abs - 4.0 * abs + 2.0;
        }
        return 0.0;
    }

    private static int interpolateArgb(int first, int second, double ratio) {
        int a = interpolate(channel(first, 0), channel(second, 0), ratio);
        int r = interpolate(channel(first, 1), channel(second, 1), ratio);
        int g = interpolate(channel(first, 2), channel(second, 2), ratio);
        int b = interpolate(channel(first, 3), channel(second, 3), ratio);
        return argb(a, r, g, b);
    }

    private static int interpolate(int first, int second, double ratio) {
        return clamp((int) Math.round(first + (second - first) * ratio), 0, 255);
    }

    private static int channel(BufferedImage image, int x, int y, int channel) {
        int safeX = clamp(x, 0, image.getWidth() - 1);
        int safeY = clamp(y, 0, image.getHeight() - 1);
        return channel(image.getRGB(safeX, safeY), channel);
    }

    private static int channel(int argb, int channel) {
        return switch (channel) {
            case 0 -> (argb >>> 24) & 0xff;
            case 1 -> (argb >>> 16) & 0xff;
            case 2 -> (argb >>> 8) & 0xff;
            case 3 -> argb & 0xff;
            default -> throw new IllegalArgumentException("通道索引必须在 0 到 3 之间");
        };
    }

    private static int argb(int a, int r, int g, int b) {
        return ((a & 0xff) << 24) | ((r & 0xff) << 16) | ((g & 0xff) << 8) | (b & 0xff);
    }

    private static int clamp(int value, int min, int max) {
        return Math.max(min, Math.min(max, value));
    }
}
