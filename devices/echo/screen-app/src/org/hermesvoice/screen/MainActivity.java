package org.hermesvoice.screen;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/**
 * One full-screen WebView showing the Hermes screen page.
 *
 * Set it up once over adb (the token goes in the address's #fragment; the page keeps it and the app
 * stores the address without it):
 *   adb shell am start -n org.hermesvoice.screen/.MainActivity \
 *     --es url 'http://HOME-ASSISTANT:8123/hermes_voice/screen?device=DEVICE_ID#token=TOKEN'
 */
public class MainActivity extends Activity {
    private static final String PREFS = "screen";
    private static final String KEY_URL = "url";
    private static final long RETRY_MS = 5000;

    private WebView web;
    private final Handler handler = new Handler();
    private final Runnable retry = new Runnable() {
        @Override
        public void run() {
            load(savedUrl());
        }
    };

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
                | WindowManager.LayoutParams.FLAG_FULLSCREEN);
        web = new WebView(this);
        web.setBackgroundColor(Color.BLACK);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return false; // stay in this view
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    // Home Assistant restarting, or Wi-Fi not up yet after boot: try again shortly.
                    handler.removeCallbacks(retry);
                    handler.postDelayed(retry, RETRY_MS);
                }
            }
        });
        setContentView(web);
        hideSystemUi();
        handleIntent(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        handleIntent(intent);
    }

    private void handleIntent(Intent intent) {
        String url = intent == null ? null : intent.getStringExtra(KEY_URL);
        if (url != null && !url.isEmpty()) {
            int hash = url.indexOf('#');
            // The token rides along once in the fragment; the page stores it itself.
            getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                    .putString(KEY_URL, hash >= 0 ? url.substring(0, hash) : url).apply();
            load(url);
        } else if (web.getUrl() == null) {
            load(savedUrl());
        }
    }

    private String savedUrl() {
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        return prefs.getString(KEY_URL, null);
    }

    private void load(String url) {
        if (url == null) {
            web.loadData(HELP, "text/html", "utf-8");
        } else {
            web.loadUrl(url);
        }
    }

    private void hideSystemUi() {
        web.setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) hideSystemUi();
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
    }

    @Override
    protected void onPause() {
        web.onPause();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacks(retry);
        web.destroy();
        super.onDestroy();
    }

    @Override
    public void onBackPressed() {
        // A screen, not a browser: nothing to go back to. hassmic's band handles Back between apps.
    }

    private static final String HELP = "<body style='background:#07080c;color:#a4acbd;font:22px sans-serif;"
            + "padding:60px'><h1 style='color:#eef1f7;font-weight:400'>Hermes Screen</h1>"
            + "<p>Not set up yet. From a computer with adb:</p><p style='color:#eef1f7'>adb shell am start "
            + "-n org.hermesvoice.screen/.MainActivity --es url "
            + "'http://HOME-ASSISTANT:8123/hermes_voice/screen?device=DEVICE_ID#token=TOKEN'</p></body>";
}
