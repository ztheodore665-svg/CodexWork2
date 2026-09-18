/** 三种图像插值方法。 */
public enum InterpolationMethod {
    NEAREST("nearest", "最近邻插值"),
    BILINEAR("bilinear", "双线性插值"),
    BICUBIC("bicubic", "双三次插值");

    private final String cliName;
    private final String displayName;

    InterpolationMethod(String cliName, String displayName) {
        this.cliName = cliName;
        this.displayName = displayName;
    }

    public String cliName() {
        return cliName;
    }

    public String displayName() {
        return displayName;
    }

    public static InterpolationMethod fromCliName(String name) {
        for (InterpolationMethod method : values()) {
            if (method.cliName.equalsIgnoreCase(name)) {
                return method;
            }
        }
        throw new IllegalArgumentException("不支持的插值方法: " + name
                + "，可选值为 nearest、bilinear、bicubic");
    }
}
