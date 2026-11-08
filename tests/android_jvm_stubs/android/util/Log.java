package android.util;

/** JVM-only test double: Android Log has no implementation in android.jar. */
public final class Log {
    public static int i(String tag, String message) { return 0; }
    public static int d(String tag, String message) { return 0; }
    public static int w(String tag, String message) { return 0; }
    public static int w(String tag, String message, Throwable error) { return 0; }
    public static int e(String tag, String message) { return 0; }
    public static int e(String tag, String message, Throwable error) { return 0; }
}
