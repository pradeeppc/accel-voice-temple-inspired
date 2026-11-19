package com.accelvoice;

import android.app.Activity;
import android.content.Intent;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.media.AudioAttributes;
import android.media.AudioManager;
import android.media.MediaPlayer;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.view.WindowManager;
import android.widget.TextView;

import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.util.Locale;

/**
 * Records the accelerometer and gyroscope at their fastest rate while (optionally) playing an
 * audio file through the phone's own speaker.
 *
 * Driven from the laptop with adb:
 *   am start -n com.accelvoice/.MainActivity --es tag NAME [--es audio FILE.wav] [--ei dur_ms N]
 *
 * FILE.wav is read from, and NAME.csv / NAME.done are written to, the app's external files dir:
 *   /sdcard/Android/data/com.accelvoice/files/
 *
 * CSV columns: sensor (a=accelerometer, g=gyroscope), t_ns (sensor timestamp), x, y, z
 */
public class MainActivity extends Activity implements SensorEventListener {

    private static final int LEAD_MS = 1500;   // silence recorded before playback (noise baseline)
    private static final int TAIL_MS = 1500;   // silence recorded after playback

    private SensorManager sensorManager;
    private HandlerThread sensorThread;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private TextView status;

    private StringBuilder csv;
    private volatile boolean recording;
    private int accelCount, gyroCount;
    private long firstNs, lastNs;

    private MediaPlayer player;
    private int savedVolume = -1;
    private String tag;
    private File dir;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        status = new TextView(this);
        status.setTextSize(20);
        status.setPadding(40, 80, 40, 40);
        setContentView(status);
        sensorManager = (SensorManager) getSystemService(SENSOR_SERVICE);
        dir = getExternalFilesDir(null);
        handle(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        handle(intent);
    }

    private void handle(Intent intent) {
        if (recording) {
            show("Busy: a recording is already running.");
            return;
        }
        tag = intent.getStringExtra("tag");
        if (tag == null) {
            Sensor a = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
            show("AccelVoice ready.\n\nAccelerometer: " + a.getName()
                    + String.format(Locale.US, "\nMax rate: %.0f Hz", 1e6 / a.getMinDelay())
                    + "\n\nFiles: " + dir);
            return;
        }
        String audio = intent.getStringExtra("audio");
        int durMs = intent.getIntExtra("dur_ms", 10000);
        new File(dir, tag + ".done").delete();
        startRecording();
        if (audio != null) {
            show("Recording " + tag + "\nlead-in silence...");
            final File f = new File(dir, audio);
            ui.postDelayed(new Runnable() { public void run() { play(f); } }, LEAD_MS);
        } else {
            show("Recording " + tag + " for " + durMs / 1000 + " s (no audio)");
            ui.postDelayed(finisher, durMs);
        }
    }

    private void startRecording() {
        csv = new StringBuilder(4 << 20);
        csv.append("sensor,t_ns,x,y,z\n");
        accelCount = gyroCount = 0;
        firstNs = lastNs = 0;
        sensorThread = new HandlerThread("sensors");
        sensorThread.start();
        Handler h = new Handler(sensorThread.getLooper());
        recording = true;
        // samplingPeriodUs = 0 means "as fast as the hardware allows".
        sensorManager.registerListener(this,
                sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER), 0, h);
        sensorManager.registerListener(this,
                sensorManager.getDefaultSensor(Sensor.TYPE_GYROSCOPE), 0, h);
    }

    private void play(File f) {
        if (!f.exists()) {
            show("Missing audio file: " + f);
            finishRecording();
            return;
        }
        try {
            AudioManager am = (AudioManager) getSystemService(AUDIO_SERVICE);
            savedVolume = am.getStreamVolume(AudioManager.STREAM_MUSIC);
            am.setStreamVolume(AudioManager.STREAM_MUSIC,
                    am.getStreamMaxVolume(AudioManager.STREAM_MUSIC), 0);
            player = new MediaPlayer();
            player.setAudioAttributes(new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build());
            player.setDataSource(f.getAbsolutePath());
            player.prepare();
            player.setOnCompletionListener(new MediaPlayer.OnCompletionListener() {
                public void onCompletion(MediaPlayer mp) {
                    synchronized (MainActivity.this) {
                        csv.append(String.format(Locale.US, "play_end,%d,0,0,0\n",
                                android.os.SystemClock.elapsedRealtimeNanos()));
                    }
                    show("Recording " + tag + "\ntail silence...");
                    ui.postDelayed(finisher, TAIL_MS);
                }
            });
            synchronized (this) {
                csv.append(String.format(Locale.US, "play_start,%d,0,0,0\n",
                        android.os.SystemClock.elapsedRealtimeNanos()));
            }
            player.start();
            show("Recording " + tag + "\nplaying " + f.getName()
                    + String.format(Locale.US, " (%.1f s)", player.getDuration() / 1000.0));
        } catch (Exception e) {
            show("Playback failed: " + e);
            finishRecording();
        }
    }

    private void finishRecording() {
        if (!recording) return;
        recording = false;
        sensorManager.unregisterListener(this);
        sensorThread.quitSafely();
        if (player != null) {
            player.release();
            player = null;
        }
        if (savedVolume >= 0) {
            ((AudioManager) getSystemService(AUDIO_SERVICE))
                    .setStreamVolume(AudioManager.STREAM_MUSIC, savedVolume, 0);
            savedVolume = -1;
        }
        double secs = (lastNs - firstNs) / 1e9;
        String summary = String.format(Locale.US,
                "%s done.\naccel: %d samples (%.1f Hz)\ngyro: %d samples (%.1f Hz)",
                tag, accelCount, accelCount / secs, gyroCount, gyroCount / secs);
        try {
            try (BufferedWriter w = new BufferedWriter(new FileWriter(new File(dir, tag + ".csv")))) {
                synchronized (this) {
                    w.write(csv.toString());
                }
            }
            try (FileWriter w = new FileWriter(new File(dir, tag + ".done"))) {
                w.write(summary + "\n");
            }
        } catch (Exception e) {
            summary = "Write failed: " + e;
        }
        csv = null;
        show(summary);
    }

    @Override
    public void onSensorChanged(SensorEvent e) {
        if (!recording) return;
        char kind = e.sensor.getType() == Sensor.TYPE_ACCELEROMETER ? 'a' : 'g';
        synchronized (this) {
            if (csv == null) return;
            csv.append(kind).append(',').append(e.timestamp).append(',')
                    .append(e.values[0]).append(',').append(e.values[1]).append(',')
                    .append(e.values[2]).append('\n');
        }
        if (kind == 'a') accelCount++; else gyroCount++;
        if (firstNs == 0) firstNs = e.timestamp;
        lastNs = e.timestamp;
    }

    @Override
    public void onAccuracyChanged(Sensor sensor, int accuracy) { }

    private void show(final String s) {
        ui.post(new Runnable() { public void run() { status.setText(s); } });
    }

    private final Runnable finisher = new Runnable() {
        public void run() { finishRecording(); }
    };

    @Override
    protected void onDestroy() {
        finishRecording();
        super.onDestroy();
    }
}
