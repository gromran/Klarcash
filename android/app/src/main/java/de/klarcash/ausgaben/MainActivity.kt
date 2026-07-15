package de.klarcash.ausgaben

import android.annotation.SuppressLint
import android.app.Activity
import android.content.ContentValues
import android.net.Uri
import android.os.Bundle
import android.provider.MediaStore
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.Socket
import java.net.URL

/**
 * Hosts the embedded Flask app in a WebView. Flask runs on a background
 * daemon thread bound to 127.0.0.1 only - no remote server is involved.
 */
class MainActivity : Activity() {

    private lateinit var webView: WebView
    private val port = 5000
    private val exportPathMarker = "/berichte/export.csv"

    companion object {
        // Guards against starting Flask twice (e.g. on Activity recreation
        // after rotation) while the process is still alive, which would
        // otherwise fail with "Address already in use".
        @Volatile
        private var flaskStarted = false
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }

        webView = findViewById(R.id.webView)
        setupWebView()

        val dbPath = File(applicationContext.filesDir, "ausgaben.db").absolutePath
        startFlaskOnce(dbPath)
        waitForServerThenLoad()
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        webView.settings.javaScriptEnabled = true
        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, url: String): Boolean {
                if (url.contains(exportPathMarker)) {
                    downloadCsv(url)
                    return true
                }
                return false
            }
        }
    }

    private fun startFlaskOnce(dbPath: String) {
        synchronized(Companion) {
            if (flaskStarted) return
            flaskStarted = true
        }
        Thread {
            val appModule = Python.getInstance().getModule("app")
            appModule.callAttr("start", "127.0.0.1", port, dbPath)
        }.apply { isDaemon = true; start() }
    }

    /** No callback exists from app.run(), so poll the loopback port from a
     *  plain Kotlin thread before pointing the WebView at it. */
    private fun waitForServerThenLoad() {
        Thread {
            val ready = (1..50).any {
                try {
                    Socket("127.0.0.1", port).use { true }
                } catch (e: IOException) {
                    Thread.sleep(100)
                    false
                }
            }
            runOnUiThread {
                if (ready) {
                    webView.loadUrl("http://127.0.0.1:$port/")
                } else {
                    Toast.makeText(this, "Server konnte nicht gestartet werden.", Toast.LENGTH_LONG).show()
                }
            }
        }.start()
    }

    /** Refetches the CSV export URL (loopback, effectively instant) and
     *  writes it into the real Downloads folder via MediaStore, since a
     *  WebView doesn't natively save Content-Disposition: attachment
     *  responses the way a browser tab does. */
    private fun downloadCsv(url: String) {
        Thread {
            try {
                val connection = URL(url).openConnection() as HttpURLConnection
                connection.connect()
                val disposition = connection.getHeaderField("Content-Disposition") ?: ""
                val filename = Regex("filename=([^;]+)").find(disposition)
                    ?.groupValues?.get(1)?.trim() ?: "bericht.csv"
                val bytes = connection.inputStream.use { it.readBytes() }
                connection.disconnect()

                val values = ContentValues().apply {
                    put(MediaStore.Downloads.DISPLAY_NAME, filename)
                    put(MediaStore.Downloads.MIME_TYPE, "text/csv")
                    put(MediaStore.Downloads.IS_PENDING, 1)
                }
                val uri: Uri? = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                uri?.let {
                    contentResolver.openOutputStream(it)?.use { out -> out.write(bytes) }
                    values.clear()
                    values.put(MediaStore.Downloads.IS_PENDING, 0)
                    contentResolver.update(it, values, null, null)
                }

                runOnUiThread {
                    Toast.makeText(this, "Bericht gespeichert: $filename", Toast.LENGTH_LONG).show()
                }
            } catch (e: Exception) {
                runOnUiThread {
                    Toast.makeText(this, "CSV-Export fehlgeschlagen: ${e.message}", Toast.LENGTH_LONG).show()
                }
            }
        }.start()
    }
}
