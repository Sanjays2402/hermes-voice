package org.hermesvoice.eye;

import android.app.Notification;
import android.app.Service;
import android.content.Intent;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.ImageFormat;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.graphics.SurfaceTexture;
import android.graphics.YuvImage;
import android.hardware.Camera;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Base64;
import android.util.Log;
import android.view.Gravity;
import android.graphics.Outline;
import android.view.TextureView;
import android.view.View;
import android.view.ViewOutlineProvider;
import android.view.WindowManager;
import android.widget.FrameLayout;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * The Echo Show's camera over HTTP, for a vision service on the LAN.
 *
 *   GET /health          "ok" (no token)
 *   GET /status          JSON: camera state, size, brightness of the last frame, clients
 *   GET /snapshot.jpg    one fresh picture (opens the camera if needed; ?q=1..100 JPEG quality)
 *   GET /stream.mjpeg    pictures as multipart MJPEG while the connection is open (?fps=1..15, default 5)
 *   GET /video?on=1|0|keep  video mode: the live picture in a window in the screen's bottom-right corner
 *                        (mirrored, like a video call). on=1 turns it on, keep extends it (it ends by itself
 *                        VIDEO_KEEP_MS after the last on/keep), on=0 ends it. It also ends when the shutter
 *                        closes or the mute button cuts the camera. From the Echo itself (the Hermes screen,
 *                        over loopback) it needs no token; the answer is JSON with CORS open.
 *
 * Every request but /health needs the token from files/token (written over adb, see docs/eye.md), as
 * "Authorization: Bearer <token>" or HTTP Basic with the token as the password.
 *
 * Privacy: the camera is open only while a request or a stream needs it, then closed after IDLE_MS, and
 * a red dot shows in the screen's top-right corner for exactly as long as it is open. With the Echo
 * muted the kernel cuts the sensor (state "blocked"). With the shutter closed the picture goes flat grey:
 * /snapshot.jpg answers 423 with "covered": true instead of a picture. Nothing is stored.
 */
public class EyeService extends Service {
    static final String TAG = "HermesEye";
    static final int PORT = 28940; // echo-dot-assist's firewall lets in TCP 16384-32767
    static final long IDLE_MS = 15000;
    static final int SETTLE_FRAMES = 8; // exposure settles over the first frames after opening
    static final int DARK = 12; // mean luma (0-255) below this: a dark room
    // Luma standard deviation below this: the shutter is closed. Measured on an Echo Show 5 1st gen:
    // about 60 open, 3 with the shutter closed (auto-exposure keeps the flat grey at ~45 brightness).
    static final int COVERED = 10;
    static final int MAX_CLIENTS = 4;
    static final long VIDEO_KEEP_MS = 60000;
    static final long COVERED_END_MS = 3000; // video ends after the shutter has been closed this long
    static final String VERSION = "0.4.1";

    private HandlerThread camThread;
    private Handler cam;
    private ServerSocket server;
    private volatile boolean running;
    private final AtomicInteger clients = new AtomicInteger();

    // Camera state, guarded by lock.
    private final Object lock = new Object();
    private Camera camera;
    private SurfaceTexture texture;
    private String state = "closed"; // closed | opening | open | blocked (mute or failure) | muted | failed
    private String lastError = "";
    private int width, height;
    private int[] fpsRange;
    private byte[] frame; // latest NV21 frame, copied only while someone waits or streams
    private long seq; // frames copied
    private long sinceOpen; // frames seen since the camera opened
    private int brightness = -1;
    private int contrast = -1; // standard deviation of luma: near 0 when the lens is covered
    private int wanting; // requests waiting for a frame
    private int streams; // open MJPEG streams
    private long lastUse;
    private long openedAt;

    private final Handler main = new Handler(Looper.getMainLooper());
    private View dot; // the camera-on indicator, shown while the camera is open

    // Video mode.
    private volatile boolean video;
    private volatile long videoUntil;
    private volatile String videoReason = "";
    private SurfaceTexture pipTexture; // the live window's texture, guarded by lock
    private View pip; // main thread
    private long lastFrameAt; // guarded by lock
    private long coveredSince; // guarded by lock, 0 = not covered
    private int bufferBytes; // guarded by lock

    @Override
    public void onCreate() {
        super.onCreate();
        camThread = new HandlerThread("eye-camera");
        camThread.start();
        cam = new Handler(camThread.getLooper());
        Notification n = new Notification.Builder(this)
                .setContentTitle("Hermes Eye")
                .setContentText("Camera service (camera closed until asked)")
                .setSmallIcon(android.R.drawable.ic_menu_camera)
                .build();
        startForeground(1, n);
        running = true;
        new Thread(this::serve, "eye-http").start();
        cam.postDelayed(idleCheck, 2000);
        Log.i(TAG, "started v" + VERSION + " on port " + PORT);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
        try {
            if (server != null) server.close();
        } catch (IOException ignored) {
        }
        cam.post(this::release);
        camThread.quitSafely();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    // ------------------------------------------------------------------------------------ camera --

    private final Runnable idleCheck = new Runnable() {
        @Override
        public void run() {
            boolean idle;
            synchronized (lock) {
                idle = camera != null && streams == 0 && wanting == 0 && !video
                        && SystemClock.elapsedRealtime() - lastUse > IDLE_MS;
            }
            if (video) {
                long now = SystemClock.elapsedRealtime();
                String end = null;
                synchronized (lock) {
                    if (now > videoUntil) end = "timeout";
                    else if (camera != null && now - lastFrameAt > 3000 && now - openedAt > 3000) end = "blocked";
                    else if (camera == null && ("blocked".equals(state) || "muted".equals(state))) end = "blocked";
                }
                if (end != null) videoOff(end);
            }
            if (idle) {
                Log.i(TAG, "idle: closing the camera");
                release();
            }
            if (running) cam.postDelayed(this, 2000);
        }
    };

    private final Camera.PreviewCallback onFrame = new Camera.PreviewCallback() {
        @Override
        public void onPreviewFrame(byte[] data, Camera c) {
            boolean coverEnd = false;
            synchronized (lock) {
                sinceOpen++;
                lastFrameAt = SystemClock.elapsedRealtime();
                if (video && sinceOpen % 8 == 0 && wanting == 0 && streams == 0) {
                    int[] stats = lumaStats(data, width * height);
                    brightness = stats[0];
                    contrast = stats[1];
                }
                if (video && sinceOpen > SETTLE_FRAMES) {
                    boolean covered = contrast >= 0 && contrast < COVERED;
                    if (!covered) coveredSince = 0;
                    else if (coveredSince == 0) coveredSince = lastFrameAt;
                    else if (lastFrameAt - coveredSince > COVERED_END_MS) coverEnd = true;
                }
                if (wanting > 0 || streams > 0) {
                    if (frame == null || frame.length != data.length) frame = new byte[data.length];
                    System.arraycopy(data, 0, frame, 0, data.length);
                    int[] stats = lumaStats(data, width * height);
                    brightness = stats[0];
                    contrast = stats[1];
                    seq++;
                    lock.notifyAll();
                }
            }
            c.addCallbackBuffer(data);
            if (coverEnd) videoOff("covered");
        }
    };

    /** Opens the camera on its thread if it is closed; waits up to 4 s. Returns the state. */
    private String ensureOpen() {
        synchronized (lock) {
            lastUse = SystemClock.elapsedRealtime();
            if (camera != null) return state;
        }
        final CountDownLatch done = new CountDownLatch(1);
        cam.post(() -> {
            open();
            done.countDown();
        });
        try {
            done.await(4, TimeUnit.SECONDS);
        } catch (InterruptedException ignored) {
        }
        synchronized (lock) {
            return state;
        }
    }

    /** On the camera thread. */
    private void open() {
        synchronized (lock) {
            if (camera != null) return;
            state = "opening";
        }
        Camera c = null;
        try {
            c = Camera.open(0);
            Camera.Parameters p = c.getParameters();
            Camera.Size size = pickSize(p.getSupportedPreviewSizes());
            p.setPreviewSize(size.width, size.height);
            p.setPreviewFormat(ImageFormat.NV21);
            int[] range = pickFps(p.getSupportedPreviewFpsRange());
            if (range != null) p.setPreviewFpsRange(range[0], range[1]);
            c.setParameters(p);
            SurfaceTexture target;
            synchronized (lock) {
                target = video ? pipTexture : null;
            }
            // In video mode the preview draws into the live window; otherwise into an unseen texture.
            SurfaceTexture tex = target == null ? new SurfaceTexture(10) : null;
            c.setPreviewTexture(target != null ? target : tex);
            int bytes = size.width * size.height * ImageFormat.getBitsPerPixel(ImageFormat.NV21) / 8;
            synchronized (lock) {
                bufferBytes = bytes;
            }
            for (int i = 0; i < 3; i++) c.addCallbackBuffer(new byte[bytes]);
            c.setPreviewCallbackWithBuffer(onFrame);
            c.setErrorCallback((error, cc) -> {
                Log.w(TAG, "camera error " + error);
                synchronized (lock) {
                    lastError = "camera error " + error;
                }
                cam.post(this::release);
            });
            c.startPreview();
            synchronized (lock) {
                camera = c;
                texture = tex;
                width = size.width;
                height = size.height;
                fpsRange = range;
                sinceOpen = 0;
                coveredSince = 0;
                state = "open";
                openedAt = SystemClock.elapsedRealtime();
                lastFrameAt = openedAt;
                lastError = "";
            }
            showDot(true);
            Log.i(TAG, "camera open " + size.width + "x" + size.height);
        } catch (RuntimeException | IOException e) {
            if (c != null) c.release();
            String muted = readGating();
            synchronized (lock) {
                // The kernel refuses the sensor while the Echo is muted ("GATING Mode is ON").
                state = blockedState(muted);
                lastError = BLOCKED + " (" + e.getMessage() + ")";
                lock.notifyAll();
            }
            Log.w(TAG, "camera open failed (" + state + "): " + e);
        }
    }

    /** On the camera thread. */
    private void release() {
        Camera c;
        SurfaceTexture t;
        synchronized (lock) {
            c = camera;
            t = texture;
            camera = null;
            texture = null;
            frame = null;
            if (!"muted".equals(state) && !"blocked".equals(state)) state = "closed";
            lock.notifyAll();
        }
        if (c != null) {
            try {
                c.setPreviewCallbackWithBuffer(null);
                c.stopPreview();
            } catch (RuntimeException ignored) {
            }
            c.release();
        }
        if (t != null) t.release();
        showDot(false);
    }

    /** A fresh NV21 frame (taken after this call), or null with the reason in state. */
    private byte[] grab(long timeoutMs) {
        String s = ensureOpen();
        if (!"open".equals(s)) return null;
        synchronized (lock) {
            wanting++;
            try {
                long need = seq + 1;
                long end = SystemClock.elapsedRealtime() + timeoutMs;
                while (camera != null && (seq < need || sinceOpen < SETTLE_FRAMES)) {
                    long left = end - SystemClock.elapsedRealtime();
                    if (left <= 0) {
                        // Frames stop when the mute button cuts the sensor: close, so the next request
                        // reopens cleanly (and the dot goes off).
                        lastError = "no frame within " + timeoutMs + " ms";
                        stalled = true;
                        return null;
                    }
                    lock.wait(left);
                }
                lastUse = SystemClock.elapsedRealtime();
                return frame == null ? null : frame.clone();
            } catch (InterruptedException e) {
                return null;
            } finally {
                wanting--;
                if (stalled) {
                    stalled = false;
                    state = blockedState(readGating());
                    lastError = BLOCKED;
                    cam.post(this::release);
                }
            }
        }
    }

    private boolean stalled; // guarded by lock

    private byte[] jpeg(byte[] nv21, int quality) {
        int w, h;
        synchronized (lock) {
            w = width;
            h = height;
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream(w * h / 4);
        new YuvImage(nv21, ImageFormat.NV21, w, h, null).compressToJpeg(new Rect(0, 0, w, h), quality, out);
        return out.toByteArray();
    }

    private static Camera.Size pickSize(List<Camera.Size> sizes) {
        // Largest up to 1280x720: the OV9734's full frame; anything bigger would be upscaled.
        Camera.Size best = sizes.get(0);
        long bestArea = -1;
        for (Camera.Size s : sizes) {
            long area = (long) s.width * s.height;
            if (s.width <= 1280 && s.height <= 720 && area > bestArea) {
                best = s;
                bestArea = area;
            }
        }
        return best;
    }

    private static int[] pickFps(List<int[]> ranges) {
        // The lowest steady rate of at least 15 fps: plenty for a vision feed, and spares the mt8163.
        int[] best = null;
        if (ranges == null) return null;
        for (int[] r : ranges) {
            if (r[1] >= 15000 && (best == null || r[1] < best[1] || (r[1] == best[1] && r[0] > best[0]))) best = r;
        }
        return best;
    }

    /** Mean and standard deviation of the luma plane, sampled. */
    private static int[] lumaStats(byte[] nv21, int pixels) {
        long sum = 0, sq = 0;
        int n = 0;
        for (int i = 0; i < pixels; i += 101) {
            int y = nv21[i] & 0xff;
            sum += y;
            sq += (long) y * y;
            n++;
        }
        if (n == 0) return new int[] {-1, -1};
        double mean = (double) sum / n;
        return new int[] {(int) mean, (int) Math.sqrt(Math.max(0, (double) sq / n - mean * mean))};
    }

    // --------------------------------------------------------------------------------- indicator --

    /** A red dot in the right-hand margin of the screen, over every app, while the camera is open. */
    private void showDot(final boolean on) {
        main.post(() -> {
            WindowManager wm = (WindowManager) getSystemService(Context.WINDOW_SERVICE);
            if (on && dot == null) {
                View v = new DotView(this);
                float d = getResources().getDisplayMetrics().density;
                WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                        (int) (64 * d + 0.5f), (int) (24 * d + 0.5f),
                        WindowManager.LayoutParams.TYPE_SYSTEM_ERROR, // above the home app and dialogs
                        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
                                | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                        PixelFormat.TRANSLUCENT);
                lp.gravity = Gravity.TOP | Gravity.END;
                lp.x = (int) (8 * d); // tucked into the top-right corner, like a camcorder's REC
                lp.y = (int) (8 * d);
                try {
                    wm.addView(v, lp);
                    dot = v;
                } catch (RuntimeException e) {
                    Log.w(TAG, "no overlay permission for the camera dot: " + e);
                }
            } else if (!on && dot != null) {
                try {
                    wm.removeView(dot);
                } catch (RuntimeException ignored) {
                }
                dot = null;
            }
        });
    }

    /** A camcorder-style "● REC" badge: dark pill, red dot that pulses gently, white letters. */
    static final class DotView extends View {
        private final Paint pill = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint dot = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final float d;
        private final long start = SystemClock.uptimeMillis();

        DotView(Context c) {
            super(c);
            d = c.getResources().getDisplayMetrics().density;
            pill.setColor(Color.argb(170, 0, 0, 0));
            dot.setColor(Color.rgb(240, 40, 40));
            text.setColor(Color.WHITE);
            text.setTextSize(13 * d);
            text.setFakeBoldText(true);
            text.setLetterSpacing(0.08f);
        }

        @Override
        protected void onDraw(Canvas canvas) {
            float h = getHeight(), w = getWidth(), r = h / 2f;
            canvas.drawRoundRect(0, 0, w, h, r, r, pill);
            double phase = ((SystemClock.uptimeMillis() - start) % 1600) / 1600.0 * 2 * Math.PI;
            dot.setAlpha((int) (175 + 80 * Math.cos(phase)));
            canvas.drawCircle(r + 1 * d, r, 5 * d, dot);
            Paint.FontMetrics fm = text.getFontMetrics();
            canvas.drawText("REC", r + 10 * d, r - (fm.ascent + fm.descent) / 2f, text);
            postInvalidateDelayed(80);
        }
    }

    static final String BLOCKED = "the camera is cut off: the Echo's mute button is on, or the camera failed";

    /** "muted" when the gating state can be read, else "blocked" (an app can't read it on Fire OS 6). */
    private static String blockedState(String gating) {
        return "1".equals(gating) ? "muted" : "0".equals(gating) ? "failed" : "blocked";
    }

    private static String readGating() {
        try (BufferedReader r = new BufferedReader(new InputStreamReader(
                new FileInputStream("/sys/devices/platform/amazon-gating/state"), StandardCharsets.US_ASCII))) {
            String line = r.readLine();
            return line == null ? "" : line.trim();
        } catch (IOException | SecurityException e) {
            return ""; // not readable from an app on this firmware: report "blocked"
        }
    }

    // -------------------------------------------------------------------------------------- http --

    private void serve() {
        try {
            server = new ServerSocket();
            server.setReuseAddress(true);
            server.bind(new InetSocketAddress(PORT));
            while (running) {
                final Socket s = server.accept();
                if (clients.get() >= MAX_CLIENTS) {
                    reply(s, 503, "text/plain", "busy\n".getBytes(StandardCharsets.US_ASCII), null);
                    s.close();
                    continue;
                }
                clients.incrementAndGet();
                new Thread(() -> {
                    try {
                        handle(s);
                    } catch (IOException | RuntimeException e) {
                        Log.d(TAG, "client: " + e);
                    } finally {
                        clients.decrementAndGet();
                        try {
                            s.close();
                        } catch (IOException ignored) {
                        }
                    }
                }, "eye-client").start();
            }
        } catch (IOException e) {
            if (running) Log.e(TAG, "server stopped: " + e);
        }
    }

    private void handle(Socket s) throws IOException {
        s.setSoTimeout(10000);
        InputStream in = s.getInputStream();
        String requestLine = readLine(in);
        if (requestLine == null) return;
        String auth = null;
        for (String line = readLine(in); line != null && !line.isEmpty(); line = readLine(in)) {
            int colon = line.indexOf(':');
            if (colon > 0 && line.substring(0, colon).trim().equalsIgnoreCase("authorization")) {
                auth = line.substring(colon + 1).trim();
            }
        }
        String[] parts = requestLine.split(" ");
        if (parts.length < 2 || !parts[0].equals("GET")) {
            reply(s, 405, "text/plain", "GET only\n".getBytes(StandardCharsets.US_ASCII), null);
            return;
        }
        String target = parts[1];
        String path = target.contains("?") ? target.substring(0, target.indexOf('?')) : target;
        String query = target.contains("?") ? target.substring(target.indexOf('?') + 1) : "";

        if (path.equals("/health")) {
            reply(s, 200, "text/plain", "ok\n".getBytes(StandardCharsets.US_ASCII), null);
            return;
        }
        if (path.equals("/video") && s.getInetAddress().isLoopbackAddress()) {
            videoRequest(s, query); // the Hermes screen on this Echo
            return;
        }
        String token = token();
        if (token.isEmpty()) {
            reply(s, 503, "application/json", json("{\"error\":\"no token set (see docs/eye.md)\"}"), null);
            return;
        }
        if (!authorized(auth, token)) {
            reply(s, 401, "application/json", json("{\"error\":\"unauthorized\"}"),
                    "WWW-Authenticate: Basic realm=\"Hermes Eye\"\r\n");
            return;
        }
        switch (path) {
            case "/status":
                reply(s, 200, "application/json", json(status()), null);
                return;
            case "/snapshot.jpg":
                snapshot(s, intParam(query, "q", 85, 30, 100));
                return;
            case "/video":
                videoRequest(s, query);
                return;
            case "/stream.mjpeg":
                stream(s, intParam(query, "fps", 5, 1, 15));
                return;
            default:
                reply(s, 404, "application/json", json("{\"error\":\"not found\"}"), null);
        }
    }

    private void snapshot(Socket s, int quality) throws IOException {
        long t0 = SystemClock.elapsedRealtime();
        byte[] nv21 = grab(3000);
        if (nv21 == null) {
            reply(s, 423, "application/json", json(status()), null);
            return;
        }
        int b;
        boolean covered;
        synchronized (lock) {
            b = brightness;
            covered = contrast >= 0 && contrast < COVERED;
        }
        if (covered) { // the shutter is closed: say so instead of sending a grey picture
            reply(s, 423, "application/json", json(status()), null);
            return;
        }
        byte[] jpg = jpeg(nv21, quality);
        reply(s, 200, "image/jpeg", jpg, extraHeaders(b, SystemClock.elapsedRealtime() - t0));
    }

    private void stream(Socket s, int fps) throws IOException {
        if (!"open".equals(ensureOpen())) {
            reply(s, 423, "application/json", json(status()), null);
            return;
        }
        synchronized (lock) {
            streams++;
        }
        Log.i(TAG, "stream started at " + fps + " fps");
        try {
            OutputStream out = s.getOutputStream();
            out.write(("HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=eyeframe\r\n"
                    + "Cache-Control: no-store\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
            long period = 1000 / fps;
            while (running) {
                long t0 = SystemClock.elapsedRealtime();
                byte[] nv21 = grab(3000);
                if (nv21 == null) break; // muted, error or closed: the client sees the stream end
                byte[] jpg = jpeg(nv21, 75);
                int b;
                synchronized (lock) {
                    b = brightness;
                }
                out.write(("--eyeframe\r\nContent-Type: image/jpeg\r\nContent-Length: " + jpg.length + "\r\n"
                        + extraHeaders(b, 0) + "\r\n").getBytes(StandardCharsets.US_ASCII));
                out.write(jpg);
                out.write("\r\n".getBytes(StandardCharsets.US_ASCII));
                out.flush();
                long wait = period - (SystemClock.elapsedRealtime() - t0);
                if (wait > 0) SystemClock.sleep(wait);
            }
        } finally {
            synchronized (lock) {
                streams--;
                lastUse = SystemClock.elapsedRealtime();
            }
            Log.i(TAG, "stream ended");
        }
    }

    // ------------------------------------------------------------------------------------- video --

    private void videoRequest(Socket s, String query) throws IOException {
        String on = "";
        for (String kv : query.split("&")) if (kv.startsWith("on=")) on = kv.substring(3);
        if (on.equals("1")) {
            videoUntil = SystemClock.elapsedRealtime() + VIDEO_KEEP_MS;
            if (!video) videoOn();
        } else if (on.equals("keep") && video) {
            videoUntil = SystemClock.elapsedRealtime() + VIDEO_KEEP_MS;
        } else if (on.equals("0") && video) {
            videoOff("asked");
        }
        String body;
        synchronized (lock) {
            body = String.format(Locale.US, "{\"video\":%s,\"reason\":\"%s\",\"state\":\"%s\",\"covered\":%s}",
                    video, videoReason, state, contrast >= 0 && contrast < COVERED);
        }
        reply(s, 200, "application/json", json(body), "Access-Control-Allow-Origin: *\r\n");
    }

    private void videoOn() {
        video = true;
        videoReason = "";
        Log.i(TAG, "video on");
        showPip(true); // the camera (re)opens into the window once its texture is ready
    }

    private void videoOff(String reason) {
        if (!video) return;
        video = false;
        videoReason = reason;
        Log.i(TAG, "video off: " + reason);
        cam.post(() -> {
            boolean busy;
            synchronized (lock) {
                busy = wanting > 0 || streams > 0;
            }
            // A picture is being taken: move the preview back to an unseen texture instead of closing.
            if (busy) retarget(null);
            else release();
        });
        showPip(false);
    }

    /**
     * On the camera thread: point the running preview at the live window (or, with null, at an unseen
     * texture) without closing the camera, so a snapshot in progress just waits a few frames. Opens the
     * camera if it is closed.
     */
    private void retarget(SurfaceTexture target) {
        Camera c;
        synchronized (lock) {
            c = camera;
        }
        if (c == null) {
            open();
            return;
        }
        try {
            SurfaceTexture unseen = target == null ? new SurfaceTexture(10) : null;
            c.stopPreview();
            c.setPreviewTexture(target != null ? target : unseen);
            int bytes;
            synchronized (lock) {
                bytes = bufferBytes;
            }
            for (int i = 0; i < 3; i++) c.addCallbackBuffer(new byte[bytes]);
            c.setPreviewCallbackWithBuffer(onFrame);
            c.startPreview();
            SurfaceTexture old;
            synchronized (lock) {
                old = texture;
                texture = unseen;
                lastFrameAt = SystemClock.elapsedRealtime();
            }
            if (old != null) old.release();
            Log.i(TAG, target != null ? "preview moved to the live window" : "preview moved off screen");
        } catch (IOException | RuntimeException e) {
            Log.w(TAG, "could not move the preview, reopening: " + e);
            release();
            open();
        }
    }

    /** The live picture: a rounded window in the bottom-right corner, mirrored like a video call. */
    private void showPip(final boolean on) {
        main.post(() -> {
            WindowManager wm = (WindowManager) getSystemService(Context.WINDOW_SERVICE);
            final float d = getResources().getDisplayMetrics().density;
            if (on && pip == null && video) {
                FrameLayout box = new FrameLayout(this);
                box.setBackgroundColor(Color.BLACK);
                box.setOutlineProvider(new ViewOutlineProvider() {
                    @Override
                    public void getOutline(View v, Outline o) {
                        o.setRoundRect(0, 0, v.getWidth(), v.getHeight(), 14 * d);
                    }
                });
                box.setClipToOutline(true);
                TextureView tv = new TextureView(this);
                tv.setScaleX(-1f);
                tv.setSurfaceTextureListener(new TextureView.SurfaceTextureListener() {
                    @Override
                    public void onSurfaceTextureAvailable(SurfaceTexture st, int w, int h) {
                        synchronized (lock) {
                            pipTexture = st;
                        }
                        cam.post(() -> {
                            if (video) retarget(st);
                        });
                    }

                    @Override
                    public void onSurfaceTextureSizeChanged(SurfaceTexture st, int w, int h) {
                    }

                    @Override
                    public boolean onSurfaceTextureDestroyed(final SurfaceTexture st) {
                        synchronized (lock) {
                            pipTexture = null;
                        }
                        // The camera may still draw into it: stop the camera first, then free it.
                        cam.post(() -> {
                            release();
                            st.release();
                        });
                        return false;
                    }

                    @Override
                    public void onSurfaceTextureUpdated(SurfaceTexture st) {
                    }
                });
                box.addView(tv, new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT,
                        FrameLayout.LayoutParams.MATCH_PARENT));
                WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                        (int) (224 * d), (int) (126 * d), // 16:9, the camera's own shape
                        WindowManager.LayoutParams.TYPE_SYSTEM_ERROR,
                        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
                                | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
                                | WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED,
                        PixelFormat.TRANSLUCENT);
                lp.gravity = Gravity.BOTTOM | Gravity.END;
                lp.x = (int) (12 * d);
                lp.y = (int) (12 * d);
                try {
                    wm.addView(box, lp);
                    pip = box;
                } catch (RuntimeException e) {
                    Log.w(TAG, "no overlay permission for the live window: " + e);
                    video = false;
                    videoReason = "no overlay permission";
                }
            } else if (!on && pip != null) {
                try {
                    wm.removeView(pip);
                } catch (RuntimeException ignored) {
                }
                pip = null;
            }
        });
    }

    private String status() {
        synchronized (lock) {
            String s = camera == null && ("open".equals(state) || "opening".equals(state)) ? "closed" : state;
            String fps = fpsRange == null ? "null" : "[" + fpsRange[0] / 1000 + "," + fpsRange[1] / 1000 + "]";
            return String.format(Locale.US,
                    "{\"version\":\"%s\",\"state\":\"%s\",\"width\":%d,\"height\":%d,\"fps_range\":%s,"
                            + "\"brightness\":%d,\"contrast\":%d,\"dark\":%s,\"covered\":%s,\"muted\":%s,\"frames\":%d,"
                            + "\"open_s\":%d,\"streams\":%d,\"clients\":%d,\"video\":%s,\"error\":\"%s\"}",
                    VERSION, s, width, height, fps, brightness, contrast, brightness >= 0 && brightness < DARK,
                    contrast >= 0 && contrast < COVERED, gatingJson(),
                    seq, camera == null ? 0 : (SystemClock.elapsedRealtime() - openedAt) / 1000, streams,
                    clients.get(), video, lastError.replace("\\", "\\\\").replace("\"", "'"));
        }
    }

    private String extraHeaders(int brightness, long ms) {
        int c;
        synchronized (lock) {
            c = contrast;
        }
        String st = c >= 0 && c < COVERED ? "covered" : brightness >= 0 && brightness < DARK ? "dark" : "ok";
        return "X-Eye-Brightness: " + brightness + "\r\nX-Eye-Contrast: " + c + "\r\nX-Eye-State: " + st
                + "\r\nX-Eye-Ms: " + ms + "\r\n";
    }

    private static String gatingJson() {
        String g = readGating();
        return "1".equals(g) ? "true" : "0".equals(g) ? "false" : "null";
    }

    // ----------------------------------------------------------------------------------- helpers --

    private String token() {
        File f = new File(getFilesDir(), "token");
        try (BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(f),
                StandardCharsets.US_ASCII))) {
            String line = r.readLine();
            return line == null ? "" : line.trim();
        } catch (IOException e) {
            return "";
        }
    }

    private static boolean authorized(String header, String token) {
        if (header == null) return false;
        String given = null;
        if (header.regionMatches(true, 0, "Bearer ", 0, 7)) {
            given = header.substring(7).trim();
        } else if (header.regionMatches(true, 0, "Basic ", 0, 6)) {
            try {
                String pair = new String(Base64.decode(header.substring(6).trim(), Base64.DEFAULT),
                        StandardCharsets.UTF_8);
                given = pair.contains(":") ? pair.substring(pair.indexOf(':') + 1) : pair;
            } catch (IllegalArgumentException e) {
                return false;
            }
        }
        return given != null && MessageDigest.isEqual(given.getBytes(StandardCharsets.UTF_8),
                token.getBytes(StandardCharsets.UTF_8));
    }

    private static int intParam(String query, String name, int def, int min, int max) {
        for (String kv : query.split("&")) {
            if (kv.startsWith(name + "=")) {
                try {
                    return Math.max(min, Math.min(max, Integer.parseInt(kv.substring(name.length() + 1))));
                } catch (NumberFormatException e) {
                    return def;
                }
            }
        }
        return def;
    }

    private static byte[] json(String s) {
        return (s + "\n").getBytes(StandardCharsets.UTF_8);
    }

    private static String readLine(InputStream in) throws IOException {
        StringBuilder sb = new StringBuilder();
        int c;
        while ((c = in.read()) != -1) {
            if (c == '\n') break;
            if (c != '\r') sb.append((char) c);
            if (sb.length() > 8192) throw new IOException("header too long");
        }
        return c == -1 && sb.length() == 0 ? null : sb.toString();
    }

    private static void reply(Socket s, int code, String type, byte[] body, String extra) throws IOException {
        String reason = code == 200 ? "OK" : code == 401 ? "Unauthorized" : code == 404 ? "Not Found"
                : code == 405 ? "Method Not Allowed" : code == 423 ? "Locked" : "Service Unavailable";
        OutputStream out = s.getOutputStream();
        out.write(("HTTP/1.1 " + code + " " + reason + "\r\nContent-Type: " + type + "\r\nContent-Length: "
                + body.length + "\r\nCache-Control: no-store\r\nConnection: close\r\n" + (extra == null ? "" : extra)
                + "\r\n").getBytes(StandardCharsets.US_ASCII));
        out.write(body);
        out.flush();
    }
}
