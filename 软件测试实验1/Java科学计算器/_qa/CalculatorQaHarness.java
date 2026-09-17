import java.util.Locale;

public class CalculatorQaHarness {
    private static int passed;
    private static int failed;

    private static void check(String id, boolean condition, String detail) {
        if (condition) {
            passed++;
            System.out.println("PASS " + id + " | " + detail);
        } else {
            failed++;
            System.out.println("FAIL " + id + " | " + detail);
        }
    }

    private static void expression(String id, String expr, double expected, double tolerance) {
        try {
            double actual = new Calculator().evaluateExpression(expr);
            check(id, Math.abs(actual - expected) <= tolerance,
                    expr + " => actual=" + actual + ", expected=" + expected);
        } catch (Exception e) {
            check(id, false, expr + " threw " + e);
        }
    }

    private static void throwsException(String id, Runnable action) {
        try {
            action.run();
            check(id, false, "no exception");
        } catch (Exception e) {
            check(id, true, e.getClass().getSimpleName() + ": " + e.getMessage());
        }
    }

    public static void main(String[] args) throws Exception {
        expression("CALC-J-006", "2+3*4", 14, 1e-12);
        expression("CALC-J-007", "(2+3)*4", 20, 1e-12);
        expression("CALC-J-008", "2**3", 8, 1e-12);
        Calculator radianCase = new Calculator();
        radianCase.toggleMode();
        try {
            double radianSin = radianCase.evaluateExpression("sin(3.141592653589793/2)");
            check("CALC-J-014", Math.abs(radianSin - 1) <= 1e-12,
                    "rad sin(pi/2) => actual=" + radianSin);
        } catch (Exception e) {
            check("CALC-J-014", false, "rad sin(pi/2) threw " + e);
        }
        expression("CALC-J-015", "log(100)+ln(e)", 3, 1e-12);

        Calculator c = new Calculator();
        c.inputNumber("12");
        c.clearEntry();
        c.inputNumber("3");
        check("CALC-J-023", "3".equals(c.getCurrent()),
                "CE then 3 => current=" + c.getCurrent());

        Calculator continuation = new Calculator();
        continuation.inputNumber("2");
        continuation.inputOperator("+");
        continuation.inputNumber("3");
        continuation.calculate();
        continuation.inputOperator("*");
        continuation.inputNumber("4");
        continuation.calculate();
        check("CALC-J-021", "20".equals(continuation.getCurrent()),
                "(2+3)= then *4= => current=" + continuation.getCurrent());

        Calculator partialCe = new Calculator();
        partialCe.inputNumber("12");
        partialCe.inputOperator("+");
        partialCe.inputNumber("3");
        partialCe.clearEntry();
        check("CALC-J-058", "12 + 0".equals(partialCe.getCurrent()),
                "12+3 then CE => current=" + partialCe.getCurrent());

        expression("CALC-J-036", "1e-5", 1e-5, 1e-12);
        expression("CALC-J-037", "1E+3", 1000, 1e-12);
        expression("CALC-J-038", "-2**2", -4, 1e-12);

        Calculator degreeCase = new Calculator();
        try {
            double tan90 = degreeCase.evaluateExpression("tan(90)");
            check("CALC-J-013b", Double.isNaN(tan90) || Double.isInfinite(tan90),
                    "deg tan(90) => actual=" + tan90);
        } catch (Exception e) {
            check("CALC-J-013b", true, "deg tan(90) threw " + e);
        }

        Calculator errorCase = new Calculator();
        errorCase.inputNumber("1");
        errorCase.inputOperator("/");
        errorCase.inputNumber("0");
        errorCase.calculate();
        check("CALC-J-029", "错误".equals(errorCase.getCurrent()),
                "1/0 => current=" + errorCase.getCurrent());

        throwsException("CALC-J-039a", () -> new Calculator().inputNumber(null));
        throwsException("CALC-J-039b", () -> new Calculator().inputOperator(null));
        throwsException("CALC-J-039c", () -> new Calculator().inputFunction(null));
        throwsException("CALC-J-039d", () -> new Calculator().inputConstant(null));

        String originalLanguage = Locale.getDefault().toLanguageTag();
        Locale.setDefault(Locale.GERMANY);
        String localized = new Calculator().formatResult(1.234567890123456E16);
        Locale.setDefault(Locale.forLanguageTag(originalLanguage));
        check("CALC-J-034", localized.contains("."),
                "German locale scientific result=" + localized);

        System.out.println("SUMMARY passed=" + passed + " failed=" + failed);
        if (failed > 0) {
            System.exit(1);
        }
    }
}
