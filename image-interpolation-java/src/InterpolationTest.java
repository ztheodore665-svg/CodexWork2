import java.awt.Color;
import java.awt.image.BufferedImage;

/** 不依赖第三方测试框架的基础正确性测试。 */
public final class InterpolationTest {
    private InterpolationTest() {
    }

    public static void main(String[] args) {
        BufferedImage source = new BufferedImage(2, 2, BufferedImage.TYPE_INT_ARGB);
        source.setRGB(0, 0, Color.BLACK.getRGB());
        source.setRGB(1, 0, Color.WHITE.getRGB());
        source.setRGB(0, 1, Color.WHITE.getRGB());
        source.setRGB(1, 1, Color.BLACK.getRGB());

        for (InterpolationMethod method : InterpolationMethod.values()) {
            BufferedImage sameSize = ImageInterpolator.resize(source, 2, 2, method);
            assertEquals("左上边界保持不变: " + method, Color.BLACK.getRGB(), sameSize.getRGB(0, 0));
            assertEquals("右上边界保持不变: " + method, Color.WHITE.getRGB(), sameSize.getRGB(1, 0));
            assertEquals("左下边界保持不变: " + method, Color.WHITE.getRGB(), sameSize.getRGB(0, 1));
            assertEquals("右下边界保持不变: " + method, Color.BLACK.getRGB(), sameSize.getRGB(1, 1));

            BufferedImage enlarged = ImageInterpolator.resize(source, 5, 5, method);
            assertEquals("放大尺寸正确: " + method, 5, enlarged.getWidth());
            assertEquals("放大高度正确: " + method, 5, enlarged.getHeight());
        }

        BufferedImage bilinear = ImageInterpolator.resize(source, 3, 3, InterpolationMethod.BILINEAR);
        int center = bilinear.getRGB(1, 1) & 0xff;
        assertEquals("双线性中心像素应为四邻域平均值", 128, center);
        System.out.println("All interpolation tests passed.");
    }

    private static void assertEquals(String message, int expected, int actual) {
        if (expected != actual) {
            throw new AssertionError(message + ": expected=" + expected + ", actual=" + actual);
        }
    }
}
