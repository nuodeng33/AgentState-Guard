package com.agentstate.guard.qr

import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.DisposableEffectResult
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.agentstate.guard.R
import com.agentstate.guard.ui.theme.Spacing
import com.agentstate.guard.ui.theme.TextSecondary
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * Live camera viewfinder + QR analysis, lifecycle-scoped. The camera binds
 * only while this composable is in composition; every frame is converted to
 * the pure [QrPixelGrid] contract by [CameraXQrFrame] so the decoder layer
 * (QrScanner + ZXing) stays Android-free and JVM-testable. Decoded payload
 * texts and invalid outcomes surface via callbacks only — this view never
 * interprets payloads or begins pairing itself.
 */
@Composable
fun QrScanView(
    onPayloadText: (String) -> Unit,
    onInvalid: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val lifecycleOwner = LocalLifecycleOwner.current
    val scanner = remember { QrScanner(ZxingQrDecodeEngine()) }
    val analyzerExecutor = remember { Executors.newSingleThreadExecutor() }
    val invalidFlash = remember { mutableStateOf(false) }
    var cameraProvider: ProcessCameraProvider? = remember { null }

    DisposableEffect(lifecycleOwner) {
        executorShutdownOnDispose(analyzerExecutor)
    }

    Box(modifier = modifier.fillMaxSize()) {
        AndroidView(
            factory = { viewContext ->
                val previewView = PreviewView(viewContext)
                bindCamera(
                    viewContext = viewContext,
                    lifecycleOwner = lifecycleOwner,
                    previewView = previewView,
                    analyzerExecutor = analyzerExecutor,
                    scanner = scanner,
                    invalidFlash = invalidFlash,
                    onPayloadText = onPayloadText,
                    onInvalid = onInvalid,
                    onProvider = { cameraProvider = it },
                )
                previewView
            },
            modifier = Modifier.fillMaxSize(),
        )
            // HUD strip (non-blocking guidance; never writes into pairing state).
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .align(Alignment.BottomCenter)
                .background(MaterialTheme.colorScheme.surface.copy(alpha = 0.72f))
                .padding(Spacing.m),
            verticalArrangement = Arrangement.spacedBy(Spacing.xs),
        ) {
            Text(
                stringResource(R.string.pairing_scan_point_camera),
                style = MaterialTheme.typography.bodyMedium,
            )
            if (invalidFlash.value) {
                Text(
                    stringResource(R.string.pairing_scan_not_valid),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            } else {
                Text(
                    stringResource(R.string.pairing_scan_hint),
                    style = MaterialTheme.typography.bodySmall,
                    color = TextSecondary,
                )
            }
        }
    }

    DisposableEffect(Unit) {
        onDispose {
            cameraProvider?.unbindAll()
        }
    }
}

private fun bindCamera(
    viewContext: android.content.Context,
    lifecycleOwner: androidx.lifecycle.LifecycleOwner,
    previewView: PreviewView,
    analyzerExecutor: ExecutorService,
    scanner: QrScanner,
    invalidFlash: MutableState<Boolean>,
    onPayloadText: (String) -> Unit,
    onInvalid: () -> Unit,
    onProvider: (ProcessCameraProvider) -> Unit,
) {
    val future = ProcessCameraProvider.getInstance(viewContext)
    future.addListener(
        {
            runCatching {
                val provider = future.get()
                onProvider(provider)
                provider.unbindAll()
                val preview = Preview.Builder().build().apply {
                    setSurfaceProvider(previewView.surfaceProvider)
                }
                val analysis = ImageAnalysis.Builder()
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .build()
                analysis.setAnalyzer(analyzerExecutor) { imageProxy ->
                    try {
                        val grid = CameraXQrFrame(imageProxy).pixels()
                        val outcome = grid?.let { scanner.scanGrid(it) }
                        when (outcome) {
                            is QrScanner.Outcome.Found -> {
                                invalidFlash.value = false
                                onPayloadText(outcome.payloadText)
                            }
                            is QrScanner.Outcome.Invalid -> {
                                invalidFlash.value = true
                                onInvalid()
                            }
                            null -> Unit
                        }
                    } finally {
                        imageProxy.close()
                    }
                }
                provider.bindToLifecycle(
                    lifecycleOwner,
                    CameraSelector.DEFAULT_BACK_CAMERA,
                    preview,
                    analysis,
                )
            }.onFailure { failure ->
                // Camera unusable (no back camera / race during teardown):
                // this screen remains honest guidance; it never fabricates a
                // payload instead of a real scan.
                android.util.Log.d(TAG, "camera bind failed: ${failure.javaClass.simpleName}")
            }
        },
        ContextCompat.getMainExecutor(viewContext),
    )
}

private fun executorShutdownOnDispose(executor: ExecutorService): DisposableEffectResult {
    return object : DisposableEffectResult {
        override fun dispose() {
            executor.shutdown()
        }
    }
}

private const val TAG = "QrScanView"
